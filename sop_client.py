"""
sop_client.py — 119 NLP Server（機器B, 192.168.5.132:8200）HTTP 呼叫封裝。

端點（以 2026-07-15 實測 /openapi.json 為準）：
  POST /session/new              建立新 session（回傳 session_id + 開場白）
  POST /session/{id}/input       推送 caller 語句（同步 blocking，回 AI 回覆 + done）
  POST /session/{id}/observe     轉真人後餵對話（只更新摘要、不回話）  ← B 於 2026-08-28 補上
  POST /session/{id}/hangup      session 收尾（B 收到才推 SSE done 並關連線）
  GET  /session/{id}/result      取案件 JSON（done=False 時只回 {"done": false}，不含 case）
  GET  /session/{id}/case/stream 案件變動 SSE（見 sse_consumer.py）

⚠️ 除了 /call/end，任何地方都不可呼叫 hangup。
   B 端 SSE 的收線條件是「收到 hangup」，提早呼叫會讓轉真人後的續聽失效。
"""
import requests

from config import SOP_SERVER_URL, HTTP_TIMEOUT


def new_session(call_uuid: str = "") -> tuple[str, list[str]]:
    """建立 119 NLP session，回傳 (session_id, 開場白 outputs)。

    call_uuid 是這通電話在我們這邊的通話編號，跟錄音檔名用的是同一組。
    傳過去之後，NLP 那邊寫出來的案件 JSON，檔名尾碼就會用這組編號，
    測試回饋表、錄音檔、案件 JSON 三邊才能靠檔名直接對上。

    不傳的話，NLP 會退回用它自己產生的 session_id 當檔名尾碼，
    那是兩組各自獨立的編號，對不起來——回饋表上填的通話編號
    就查不出是哪一通的案件 JSON（2026-10-05 之前就是這個狀況）。

    沒有 call_uuid 時不帶 request body，行為與過去完全相同。
    """
    try:
        resp = requests.post(
            f"{SOP_SERVER_URL}/session/new",
            json={"call_uuid": call_uuid} if call_uuid else None,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("session_id", ""), data.get("outputs", [])
    except Exception as e:
        print(f"⚠️  119 NLP new session 失敗：{e}", flush=True)
        return "", []


def push(sop_session_id: str, text: str) -> tuple[list[str], bool, str | None]:
    """送 caller 文字給 119 NLP，回傳 (AI回覆outputs, done, error)。

    註：119 的 error 恆為 None，結束原因在 case["result"]（見 end_result()）。
    """
    if not sop_session_id:
        return [], False, None
    try:
        resp = requests.post(
            f"{SOP_SERVER_URL}/session/{sop_session_id}/input",
            json={"text": text}, timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("outputs", []), data.get("done", False), data.get("error")
    except Exception as e:
        print(f"⚠️  119 NLP push 失敗：{e}", flush=True)
        return [], False, None


def observe(sop_session_id: str, text: str, role: str) -> dict:
    """Bridge 後的雙通道 STT 觀察：把真人接手後的對話餵給機器B 更新 case_summary。

    機器B 2026-08-28 已實作（見 docs/talk_toB/observe-endpoint-for-machineB.md）：
      - 不看也不設 done，任何階段都能呼叫
      - 不回 AI 語句（無 outputs），本端點只寫 transcript 就回（實測 <2ms）
      - 摘要在 B 端背景重算（約 5 秒一次），更新後照常從 SSE 推 case_updated

    role: "caller"（民眾）或 "agent"（接手的真人受理員）。
    回傳值目前呼叫端不使用，失敗只 log 不拋例外（不能影響通話）。
    """
    if not sop_session_id:
        return {}
    try:
        resp = requests.post(
            f"{SOP_SERVER_URL}/session/{sop_session_id}/observe",
            json={"text": text, "role": role}, timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"⚠️  119 NLP observe 失敗（{role}）：{e}", flush=True)
        return {}


def hangup(sop_session_id: str) -> None:
    if not sop_session_id:
        return
    try:
        requests.post(f"{SOP_SERVER_URL}/session/{sop_session_id}/hangup", timeout=HTTP_TIMEOUT)
    except Exception:
        pass


def get_result(sop_session_id: str) -> dict | None:
    """取案件 dict（CaseInfo119：main_category / sub_category / address / is_ohca 等 17 欄）。

    註：通話進行中（done=False）119 只回 {"done": false}、不含 case，此時回 None。
    """
    if not sop_session_id:
        return None
    try:
        resp = requests.get(
            f"{SOP_SERVER_URL}/session/{sop_session_id}/result", timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("case")
    except Exception as e:
        print(f"⚠️  119 NLP get result 失敗：{e}", flush=True)
        return None


# 119 的結束原因放在 case["result"]（error 欄位恆為 None，與 110 不同）：
#   dispatched      救護車已派出，正常完成
#   ohca_transfer   判斷為 OHCA，需轉接真人
#   human_transfer  其他需轉接真人的情形（火警無法確認燃燒標的、報案人資訊問不出來…）
#   caller_hangup   報案人掛斷
#   manual_end      操作員主動結束流程
#   error           技術錯誤
def end_result(case: dict | None) -> str | None:
    """取 119 case 的結束原因（result 欄位）。"""
    return case.get("result") if case else None


# B 端引擎會把所有轉真人情形收斂成這兩個值之一
# （見 nlp_cyberon_server/ai/119_0813_adjustSOP/sop_119_engine.py:1005-1007）
_TRANSFER_RESULTS = {"ohca_transfer", "human_transfer"}


def is_transfer_to_human(case: dict | None) -> bool:
    """是否需轉接真人。

    ⚠️ 這個判斷若漏掉任一值，該通電話會被當成「正常結束」直接掛斷——
       民眾會聽到「立即為您轉接專人」然後電話就斷了。B 端新增結束原因時務必同步更新。
    """
    return end_result(case) in _TRANSFER_RESULTS
