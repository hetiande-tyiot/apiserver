"""
sop_client.py — 119 NLP Server（機器B, 192.168.5.132:8200）HTTP 呼叫封裝。

端點（以 2026-07-15 實測 /openapi.json 為準）：
  POST /session/new              建立新 session（回傳 session_id + 開場白）
  POST /session/{id}/input       推送 caller 語句（同步 blocking，回 AI 回覆 + done）
  POST /session/{id}/hangup      session 收尾
  GET  /session/{id}/result      取案件 JSON（done=False 時只回 {"done": false}，不含 case）

119 沒有、但 110 有的端點（勿再呼叫）：
  POST /session/{id}/observe     → 119 未提供，observe() 已改為 no-op
  GET  /session/{id}/case/stream → 119 無 SSE（sse_consumer 待移除）
"""
import requests

from config import SOP_SERVER_URL, HTTP_TIMEOUT


def new_session() -> tuple[str, list[str]]:
    """建立 119 NLP session，回傳 (session_id, 開場白 outputs)。"""
    try:
        resp = requests.post(f"{SOP_SERVER_URL}/session/new", timeout=HTTP_TIMEOUT)
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
    """Bridge 後的雙通道 STT 觀察 —— 119 未提供 /observe，本函式為 no-op。

    110LLM 有 /observe，可在轉接真人後持續更新 case_summary + transcript。
    119 NLP Server 沒有這個端點（實測 /openapi.json 僅 7 個路由），
    再呼叫只會換來 404，故直接短路返回，不送 HTTP。

    若日後 132 補上 /observe，把下面的呼叫還原即可（git 歷史有原始版本）。
    """
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
#   dispatched     救護車已派出，正常完成
#   ohca_transfer  判斷為 OHCA，需轉接真人
#   caller_hangup  報案人掛斷
#   error          技術錯誤
def end_result(case: dict | None) -> str | None:
    """取 119 case 的結束原因（result 欄位）。"""
    return case.get("result") if case else None


def is_transfer_to_human(case: dict | None) -> bool:
    """是否需轉接真人（目前僅 OHCA 判定會轉接）。"""
    return end_result(case) == "ohca_transfer"
