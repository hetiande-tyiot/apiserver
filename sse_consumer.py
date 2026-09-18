"""
sse_consumer.py — 訂閱機器B 的 case 變化 SSE 串流，把更新即時推給機器C。
（2026-06-XX 從 ai/ami/ 搬到 apiserver/，由 api_server 集中管理）

對應機器B 端點（host 跟著 config.SOP_SERVER_URL，各機器不同）：
  GET {SOP_SERVER_URL}/session/{sop_session_id}/case/stream

事件類型：
  - case_updated  data 為當前完整 case dict（130+ 欄位）
  - done          data 為 {"done": true, "error": ..., "case": final_case}
                  推完 server 主動關連線

依賴：只用 requests（stream + iter_lines 手刻 SSE parser），不裝 httpx-sse。
"""
import json
import threading
from typing import Generator

import requests

from config import SOP_SERVER_URL, AMIDAEMON_URL
from state  import mc_state, MCSessionState
from utils  import _ts
import machinec_client as machinec


def _case_summary(case: dict) -> dict:
    """擷取 Analysis 會送給 MC 的所有欄位，作為 dedup 比對的依據。
    包含 4 個基本欄位 + scenario 專屬欄位（非 null 的）。"""
    return {
        "caseTypeName": case.get("sub_category") or case.get("main_category") or "",
        "caseAddr":     case.get("address") or "",
        "caseSummary":  case.get("case_summary") or "",
        "callerPhone":  case.get("caller_contact") or "",
        "caseDetails":  machinec.extract_case_details(case),
    }


def _parse_sse(response: requests.Response) -> Generator[tuple[str, dict], None, None]:
    """從 streaming response 解析 SSE，逐筆 yield (event_type, data_dict)。
    SSE 格式：
        event: <name>
        data: <json>
        <blank line>
    """
    event_type = None
    data_lines: list[str] = []
    for line in response.iter_lines(decode_unicode=True):
        if line == "" or line is None:
            # 空行 = 一個 event 結束
            if event_type and data_lines:
                try:
                    yield event_type, json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    pass
            event_type, data_lines = None, []
            continue
        if line.startswith("event:"):
            event_type = line[6:].lstrip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith(":"):
            # SSE comment / keepalive，忽略
            pass


def _process_case(call_uuid: str, case: dict, is_final: bool = False) -> None:
    """收到 case 後 dedup、推 Analysis 給 MC。

    is_final=True 時**不**送 Analysis(Final)——改成 push /end_call 給 amidaemon、
    讓通話走完整 Hangup 流程，由 /call/end 在 Hangup 後送 Final。
    這樣 MC 收到 Final 時序才正確：所有 Conversation/Updated 都在 Final 之前出現。"""
    st = mc_state.get(call_uuid)
    if not st:
        return

    if is_final:
        if st.intervened:
            # 介入引發的 done event：只是因為 hangup 解鎖 /observe，通話還在繼續
            # 不通知 amidaemon 掛斷、SSE 自然結束即可
            print(f"🏁 [{call_uuid[:8]}] SSE done（介入引發），通話續行不掛斷 {_ts()}", flush=True)
            return

        if st.should_end:
            # api_server 在 sop.push 回 done 當下已送過 /end_call，不重複送
            print(f"🏁 [{call_uuid[:8]}] SSE done（/end_call 已送過），跳過 {_ts()}", flush=True)
            return

        # 後備路徑：api_server 沒攔到 done 時才由這裡通知 amidaemon 收尾
        st.should_end = True
        print(f"🏁 [{call_uuid[:8]}] SSE done → push /end_call 給 amidaemon {_ts()}", flush=True)
        notify_amidaemon_end(call_uuid)
        return

    summary = _case_summary(case)
    if st.last_sent_case is None:
        # 首次：等 caseTypeName 有值才送 Initial
        if summary["caseTypeName"]:
            st.last_sent_case = summary
            print(f"📋 [{call_uuid[:8]}] Analysis(Initial via SSE) → 案類：{summary['caseTypeName']} {_ts()}", flush=True)
            threading.Thread(target=machinec.analysis,
                             args=(call_uuid, case, "Initial"),
                             daemon=True).start()
    else:
        if summary != st.last_sent_case:
            st.last_sent_case = summary
            print(f"📋 [{call_uuid[:8]}] Analysis(Updated via SSE) → 欄位變化 {_ts()}", flush=True)
            threading.Thread(target=machinec.analysis,
                             args=(call_uuid, case, "Updated"),
                             daemon=True).start()


def notify_amidaemon_end(call_uuid: str) -> None:
    """通知 amidaemon：AI 流程結束、該掛斷此通電話。
    呼叫 amidaemon 內部控制端點 POST /end_call。失敗只 log，不影響 SSE 流程。

    正常路徑由 api_server._push_end_call_once() 觸發（sop.push 回 done 當下）；
    本模組的 done event 分支保留為後備。"""
    try:
        resp = requests.post(
            f"{AMIDAEMON_URL}/end_call",
            json={"callId": call_uuid},
            timeout=3,
        )
        if not resp.ok:
            print(f"⚠️ /end_call 回 HTTP {resp.status_code}：{resp.text} {_ts()}", flush=True)
    except Exception as e:
        print(f"⚠️ /end_call 失敗 [{call_uuid[:8]}]：{e} {_ts()}", flush=True)


def _listen_loop(call_uuid: str, sop_session_id: str,
                 stop_event: threading.Event) -> None:
    """背景 thread 主迴圈。連線到 done 事件或外部 stop signal 為止。"""
    url = f"{SOP_SERVER_URL}/session/{sop_session_id}/case/stream"
    print(f"🔌 [{call_uuid[:8]}] SSE 連線：{url} {_ts()}", flush=True)
    try:
        with requests.get(url, stream=True, timeout=None,
                          headers={"Accept": "text/event-stream"}) as resp:
            if resp.status_code != 200:
                print(f"⚠️ [{call_uuid[:8]}] SSE 連線失敗：HTTP {resp.status_code} {_ts()}", flush=True)
                return

            for event_type, data in _parse_sse(resp):
                if stop_event.is_set():
                    break
                if event_type == "case_updated":
                    _process_case(call_uuid, data)
                elif event_type == "done":
                    print(f"✅ [{call_uuid[:8]}] SSE done（error={data.get('error')}）{_ts()}", flush=True)
                    _process_case(call_uuid, data.get("case") or {}, is_final=True)
                    break
    except Exception as e:
        print(f"⚠️ [{call_uuid[:8]}] SSE 例外：{e} {_ts()}", flush=True)
    finally:
        print(f"🔌 [{call_uuid[:8]}] SSE thread 結束 {_ts()}", flush=True)


def start(call_uuid: str, sop_session_id: str) -> threading.Event:
    """啟動 SSE consumer、建立 mc_state，回傳 stop_event 給呼叫方控制提早停止。"""
    if call_uuid not in mc_state:
        mc_state[call_uuid] = MCSessionState()
    stop_event = threading.Event()
    mc_state[call_uuid].sse_stop_event = stop_event
    threading.Thread(
        target=_listen_loop,
        args=(call_uuid, sop_session_id, stop_event),
        daemon=True,
        name=f"SSE-{call_uuid[:8]}",
    ).start()
    return stop_event


def stop(call_uuid: str) -> None:
    """通知 SSE consumer 結束（保險用，正常情況 SSE done 會自然結束）。"""
    st = mc_state.get(call_uuid)
    if st and st.sse_stop_event:
        st.sse_stop_event.set()
