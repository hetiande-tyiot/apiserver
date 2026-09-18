"""
api_server.py — 機器A 中控 API Server（FastAPI）

啟動：
    cd /home/sipuser/apiserver
    source apienv/bin/activate
    uvicorn api_server:app --host 0.0.0.0 --port 8200

模組架構：
    config.py         ← 所有環境變數與參數
    state.py          ← 程序內共用狀態（sop_sessions / turn_counters / mc_state）
    db.py             ← MySQL 連線與 CRUD
    sop_client.py     ← 機器B 110LLM HTTP 呼叫
    tts_client.py     ← Cyberon TTS gRPC + 降採樣
    machinec_client.py← 機器C HTTP 呼叫（Conversation/Analysis/TransferSuggest）
    sse_consumer.py   ← 訂閱機器B SSE，推 MC Analysis、push amidaemon /end_call
    payloads.py       ← FastAPI Pydantic models
    utils.py          ← _ts() 時間戳工具
"""
import threading
from datetime import datetime

import requests
from fastapi import FastAPI

import db
import sop_client as sop
import tts_client as tts
import machinec_client as machinec
import sse_consumer
from config    import AMIDAEMON_URL, SOP_SERVER_URL
from payloads  import CallStartPayload, SttPayload, CallEndPayload, TransferPayload, PrecheckPayload
from state     import sop_sessions, turn_counters, mc_state, MCSessionState


app = FastAPI(title="110 API Server", version="1.1")


@app.on_event("startup")
def _on_startup() -> None:
    """啟動時 fetch 機器B 的 case 欄位中文 label 對照表（給 caseDetails 用）。"""
    machinec.fetch_labels(SOP_SERVER_URL)


# ═══════════════════════════════════════════════════════════════════════════
# 內部小工具：MC 推送（dedup 邏輯集中在這）
# ═══════════════════════════════════════════════════════════════════════════
def _push_conversation_async(call_uuid: str, speaker: str, text: str) -> None:
    """非同步推 Conversation 給 MC，失敗只 log 不影響主流程。"""
    threading.Thread(
        target=machinec.conversation,
        args=(call_uuid, speaker, text),
        daemon=True,
    ).start()


def _push_transfer_suggest_once(call_uuid: str, reason: str, case: dict) -> None:
    """每通電話最多送一次 TransferSuggest。"""
    st = mc_state.get(call_uuid)
    if not st or st.transfer_suggested:
        return
    st.transfer_suggested = True
    print(f"💡 [{call_uuid[:8]}] NLP 建議轉接，通知 MC", flush=True)
    threading.Thread(
        target=machinec.transfer_suggest,
        args=(call_uuid, reason, case),
        daemon=True,
    ).start()


def _push_final_once(call_uuid: str, case: dict) -> None:
    """每通電話最多送一次 Analysis(Final)。"""
    st = mc_state.get(call_uuid)
    if not st:
        return
    if st.final_sent:
        print(f"ℹ️ [{call_uuid[:8]}] Final 已送過，跳過", flush=True)
        return
    st.final_sent = True
    threading.Thread(
        target=machinec.analysis,
        args=(call_uuid, case, "Final"),
        daemon=True,
    ).start()


def _push_end_call_once(call_uuid: str) -> None:
    """AI 流程正常結束 → 通知 amidaemon 收尾（播完最後一段 TTS 後掛斷）。每通只送一次。

    為什麼在這裡送、而不是等 SSE 的 done event：
      機器B 2026-08-28 起把 SSE 的收線條件改成「收到 /hangup」（為了讓轉真人後
      能續聽）。而我們的 /hangup 只在 /call/end 呼叫、/call/end 又要等電話掛斷，
      若仍等 SSE done 才通知 amidaemon 掛斷，就會四方互等形成死結。
      sop.push() 的回傳值本來就有 done，直接用它觸發即可，不必繞 SSE。
    """
    st = mc_state.get(call_uuid)
    if not st or st.should_end:
        return
    st.should_end = True
    print(f"🏁 [{call_uuid[:8]}] NLP done → push /end_call 給 amidaemon", flush=True)
    threading.Thread(
        target=sse_consumer.notify_amidaemon_end,
        args=(call_uuid,),
        daemon=True,
    ).start()


# ═══════════════════════════════════════════════════════════════════════════
# 通話開始：建 cases、開 110LLM session、合成開場白 TTS、啟動 SSE consumer
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/call/precheck")
def call_precheck(payload: PrecheckPayload):
    """通話進 AI 前的 pre-check：查 MC 是否滿線。

    amidaemon 在 STT_CALL_START 事件時呼叫。滿線時 amidaemon 會把 caller
    導向 [ivr-ai-full] 掛斷、不進 /call/start，避免浪費 119LLM/TTS/SSE 資源。
    """
    uuid = payload.uuid
    is_full = machinec.check_capacity(uuid)
    if is_full:
        print(f"🚫 [{uuid[:8]}] /call/precheck → 滿線", flush=True)
    return {"uuid": uuid, "is_full": is_full}


@app.post("/call/start")
def call_start(payload: CallStartPayload):
    uuid = payload.uuid
    db.insert_case(uuid, payload.channel)

    if payload.transfer_mode:
        # 轉接模式：只建 cases 紀錄，不啟動 NLP session、不啟動 SSE
        sop_sessions[uuid] = ""
        turn_counters[uuid] = 0
        print(f"🔀 [{uuid[:8]}] 轉接模式 call/start（無 NLP）", flush=True)
        return {"uuid": uuid, "outputs": [], "audio_path": "", "status": "transfer_started",
                "sop_session_id": ""}

    sop_id, greet_outputs = sop.new_session()
    sop_sessions[uuid] = sop_id
    turn_counters[uuid] = 0

    # 初始化 MC 推送狀態
    mc_state[uuid] = MCSessionState()

    audio_path = ""
    if greet_outputs:
        greet_text = " ".join(greet_outputs)
        for line in greet_outputs:
            print(f"🤖 [{uuid[:8]}] 開場白：{line}", flush=True)
            # 開場白送 MC（AI 說的第一句）
            _push_conversation_async(uuid, "AI", line)
        turn_counters[uuid] = 1
        audio_path = tts.synthesize(greet_text, uuid, 1)

    # 啟動 SSE consumer 訂閱機器B 的 case 變化推送
    if sop_id:
        sse_consumer.start(uuid, sop_id)

    return {"uuid": uuid, "outputs": greet_outputs, "audio_path": audio_path, "status": "started",
            "sop_session_id": sop_id}


# ═══════════════════════════════════════════════════════════════════════════
# 每句 STT：MySQL 存 utterance、呼叫 110LLM、合成 TTS、推 MC Conversation
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/call/stt")
def call_stt(payload: SttPayload):
    uuid = payload.uuid
    text = payload.text
    role = payload.role
    ts   = payload.timestamp or datetime.now().isoformat()

    tag = "轉接" if payload.transfer else role
    print(f"📝 [{uuid[:8]}] [{tag}] {text}", flush=True)

    db.insert_utterance(uuid, role, text, ts)

    # 推 Conversation 給 MC：Citizen / Officer 都送（在 NLP 處理前就先推）
    mc_speaker = "Officer" if role == "agent" else "Citizen"
    _push_conversation_async(uuid, mc_speaker, text)

    # ── Bridge 模式：送 /observe，不產生 AI 回覆與 TTS ──────────────────
    if payload.transfer:
        sop.observe(sop_sessions.get(uuid, ""), text, role)
        return {"uuid": uuid, "outputs": [], "audio_path": "",
                "done": False, "error": None, "transfer": False, "case": None}

    # ── 一般 AI 對話模式（僅 caller 進入此處）────────────────────────────
    nlp_outputs: list[str] = []
    audio_path = ""
    done = False
    error = None
    transfer = False
    case = None

    if role == "caller":
        sop_id = sop_sessions.get(uuid, "")
        nlp_outputs, done, error = sop.push(sop_id, text)

        if nlp_outputs:
            reply_text = " ".join(nlp_outputs)
            print(f"🤖 [{uuid[:8]}] 受理員：{reply_text}", flush=True)
            # 把 NLP 回覆推給 MC
            for line in nlp_outputs:
                _push_conversation_async(uuid, "AI", line)

            turn = turn_counters.get(uuid, 0) + 1
            turn_counters[uuid] = turn
            audio_path = tts.synthesize(reply_text, uuid, turn)

            db.insert_dialogue(uuid, turn, text, reply_text, audio_path)
            db.update_nlp(uuid, reply_text)

        # 每輪都即時抓案件當前狀態（給 done=True 時存 MySQL 用；對話中即時推送走 SSE）
        if sop_id:
            case = sop.get_result(sop_id)
            if case is not None:
                non_null = sum(1 for v in case.values() if v)
                classification = case.get("sub_category") or case.get("main_category") or "未分類"
                print(f"📋 [{uuid[:8]}] /result：{non_null} 欄有值，案類={classification}", flush=True)

        if done:
            # 119 的結束原因在 case["result"]，不在 error（error 恆為 None）
            result = sop.end_result(case)
            transfer = sop.is_transfer_to_human(case)
            if transfer:
                print(f"ℹ️  [{uuid[:8]}] 業務性結束：OHCA 轉接真人（result={result}）", flush=True)
            elif result in ("dispatched", "caller_hangup"):
                print(f"ℹ️  [{uuid[:8]}] 業務性結束：{result}", flush=True)
            elif result == "error" or error:
                print(f"🚨 [{uuid[:8]}] 技術錯誤：result={result} error={error}", flush=True)
            if case:
                db.save_case(uuid, case)

        # NLP 判定建議轉接 → 通知 MC（每通電話最多送一次）
        if transfer:
            _push_transfer_suggest_once(uuid, "out_of_scope", case or {})
        elif done:
            # AI 流程正常跑完（非轉真人）→ 通知 amidaemon 播完 TTS 後收尾掛斷。
            # 不能等 SSE 的 done event：B 端已改成收到 /hangup 才推 done，而 /hangup
            # 只在 /call/end 呼叫，等下去會互等成死結（詳見 _push_end_call_once）。
            _push_end_call_once(uuid)

    return {
        "uuid":       uuid,
        "outputs":    nlp_outputs,
        "audio_path": audio_path,
        "done":       done,
        "error":      error,
        "transfer":   transfer,
        "case":       case,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 通話結束：119LLM hangup、取最終 case、推 MC Final、收 SSE
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/call/end")
def call_end(payload: CallEndPayload):
    uuid = payload.uuid
    sop_id = sop_sessions.pop(uuid, "")
    turn_counters.pop(uuid, None)

    # 通知 SSE consumer 停（保險用，正常 SSE done 會自結束）
    sse_consumer.stop(uuid)

    sop.hangup(sop_id)

    case = sop.get_result(sop_id)
    if case:
        db.save_case(uuid, case)

    db.update_case_ended(uuid)

    # 推 Analysis(Final) 給 MC（去重）
    _push_final_once(uuid, case or {})

    # 清掉 MC 狀態
    mc_state.pop(uuid, None)

    return {"uuid": uuid, "status": "ended", "case": case}


# ═══════════════════════════════════════════════════════════════════════════
# 前端介入指令：forward 給 amidaemon
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/AiRobot/Transfer")
def ai_robot_transfer(payload: TransferPayload):
    """前端（受理員介面）發來的介入/重新派工指令。

    處理順序：
      1. 標記 mc_state.intervened = True（sse_consumer 收 done event 時不會誤觸發掛斷）
      2. Forward 給 amidaemon 執行實際的 AMI Redirect → Dial 分機

    ⚠️ 這裡**不可以**呼叫 sop.hangup()。B 端 SSE 的收線條件是「收到 hangup」，
       提早呼叫會關掉 SSE，轉真人後的摘要就再也推不過來（2026-08-28 B 端已放寬
       /observe 不需 done=true，原本的 hangup 繞道已無必要）。
    """
    uuid = payload.callId
    print(f"📥 [Transfer/{payload.reason}] {uuid[:8]} → PJSIP/{payload.agentExtension}",
          flush=True)

    # 1. 標記介入
    st = mc_state.get(uuid)
    if st:
        st.intervened = True

    # 2. Forward 給 amidaemon（不 hangup，理由見 docstring）
    try:
        resp = requests.post(
            f"{AMIDAEMON_URL}/transfer",
            json={
                "callId":         uuid,
                "agentExtension": payload.agentExtension,
                "reason":         payload.reason,
            },
            timeout=5,
        )
        return resp.json()
    except Exception as e:
        print(f"⚠️  forward to amidaemon 失敗：{e}", flush=True)
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# 健康檢查
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {"status": "ok", "active_sessions": len(sop_sessions)}
