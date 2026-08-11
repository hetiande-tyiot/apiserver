"""
machinec_client.py — 機器C（前端系統）API 呼叫封裝。
（2026-06-XX 從 ai/ami/ 搬到 apiserver/，集中所有 MC 通訊）

對外介面（均為同步，建議在 daemon thread 中呼叫，失敗只 log 不拋例外）：
  conversation(call_id, speaker, text)
      speaker: "AI" | "Citizen" | "Officer"

  analysis(call_id, case, state)
      state:   "Initial" | "Updated" | "Final"
      case:    dict（110LLM result），缺欄位時送空字串

  transfer_suggest(call_id, reason, case)
      reason:  "out_of_scope" | "user_request" | "ai_complete"
      case:    dict，可為空
"""
import json
import os
from datetime import datetime

import requests

from config import MACHINEC_URL, MACHINEC_TIMEOUT
from utils  import _ts

_CONV_URL             = f"{MACHINEC_URL}/api/AiRobot/Conversation"
_ANALYSIS_URL         = f"{MACHINEC_URL}/api/AiRobot/Analysis"
_TRANSFER_SUGGEST_URL = f"{MACHINEC_URL}/api/AiRobot/TransferSuggest"
_CAPACITY_URL         = f"{MACHINEC_URL}/api/AiRobot/Capacity"   # 規格待定
_IDLE_LINE_URL        = f"{MACHINEC_URL}/api/AiRobot/IdleLineCount"


# caseDetails 排除清單：這些不放進 caseDetails
#   - 已放頂層的：sub_category(→caseTypeName)、case_summary(→caseSummary)
#   - 太長的：transcript
#   - 內部欄位（不給受理員看）：main_conf/sub_conf（信心分數）、flow_stage/result（流程狀態）
# 註：address 不在排除清單—希望它同時出現在頂層 caseAddr 和 caseDetails 的 "現場位置"。
_BASE_FIELD_KEYS = {
    "sub_category",
    "case_summary",
    "transcript",
    "main_conf", "sub_conf",
    "flow_stage", "result",
}


# 機器B 提供的 case 欄位中文 label 對照表（api_server 啟動時 fetch 一次）
_LABEL_MAP: dict[str, str] = {}


# 機器A 端的 label override：優先於機器B 給的 label。
# 用途：當 MC UI 紅框名稱跟 B 的 label 不一致、或某個 B 排除的欄位我們仍想加入時。
# 例：location 已是頂層 caseAddr，但 MC「現場位置」紅框也要這個值。
_LABEL_OVERRIDES: dict[str, str] = {
    "address": "現場位置",
}


def _label_for(key: str) -> str:
    """取得欄位中文 label。優先級：A override > B 對照表 > 原 key（fallback）"""
    return _LABEL_OVERRIDES.get(key) or _LABEL_MAP.get(key, key)


def fetch_labels(sop_server_url: str, timeout: int = 5) -> int:
    """從機器B 抓 case 欄位中文 label 對照表，cache 在模組層級。
    失敗時 _LABEL_MAP 保持空 dict（extract_case_details 會 fallback 用英文 key）。"""
    global _LABEL_MAP
    url = f"{sop_server_url}/schema/case-fields/labels"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        _LABEL_MAP = resp.json() or {}
        print(f"📚 [MachineC] 載入 case 欄位 label：{len(_LABEL_MAP)} 條 {_ts()}", flush=True)
        return len(_LABEL_MAP)
    except Exception as e:
        print(f"⚠️ [MachineC] 載入 label 失敗：{e} {_ts()}", flush=True)
        _LABEL_MAP = {}
        return 0


def _stringify(v) -> str:
    """把 case 欄位值轉成字串給 MC（MC 規格要求 caseDetails 內全部值都是字串）。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def extract_case_details(case: dict) -> dict:
    """從整份 case 中挑出『非基本欄位、且有值』的 scenario 專屬欄位，
    用 _label_for() 取中文 label（A override > B 對照表 > 原 key），
    值全部轉成字串以符合 MC 規格。"""
    return {
        _label_for(k): _stringify(v)
        for k, v in (case or {}).items()
        if v is not None and v != "" and k not in _BASE_FIELD_KEYS
    }


def conversation(call_id: str, speaker: str, text: str) -> None:
    """送單筆對話紀錄給機器C。"""
    payload = {"callId": call_id, "speaker": speaker, "DialogueText": text}
    try:
        resp = requests.post(_CONV_URL, json=payload, timeout=MACHINEC_TIMEOUT)
        if not resp.json().get("success"):
            print(f"⚠️ [MachineC] Conversation 回傳失敗：{resp.text} {_ts()}", flush=True)
    except Exception as e:
        print(f"⚠️ [MachineC] Conversation 例外（{speaker}）：{e} {_ts()}", flush=True)


def analysis(call_id: str, case: dict, state: str) -> None:
    """送案件分析結果給機器C。case 為 110LLM result dict，可為空 dict。

    Payload 結構：
      - 5 個基本欄位（callId、caseTypeName、caseAddr、caseSummary、callerPhone）
      - analysisState（Initial/Updated/Final）
      - caseDetails：scenario 專屬的關鍵問題欄位（只放有值的）
    """
    case = case or {}
    details_dict = extract_case_details(case)
    # MC 規格要求 caseDetails 是 JSON 字串；details_dict 為空時送空物件字串 "{}"
    case_details_str = json.dumps(details_dict, ensure_ascii=False)
    payload = {
        "callId":        call_id,
        "caseTypeName":  case.get("sub_category") or case.get("main_category") or "",
        "caseAddr":      case.get("address") or "",
        "caseSummary":   case.get("case_summary") or "",
        "callerPhone":   "",   # 119 case 無電話欄位（待從 Asterisk caller ID 取得）
        "analysisState": state,
        "caseDetails":   case_details_str,
    }
    try:
        resp = requests.post(_ANALYSIS_URL, json=payload, timeout=MACHINEC_TIMEOUT)
        data = resp.json()
        if data.get("success"):
            n_details = len(details_dict)
            print(f"✅ [MachineC] Analysis({state}) 送出（caseDetails: {n_details} 欄）{_ts()}", flush=True)
        else:
            # 失敗時 dump 出我們送的 payload，方便對照 MC 的錯誤訊息
            print(f"⚠️ [MachineC] Analysis({state}) 回傳失敗：{resp.text} {_ts()}", flush=True)
            print(f"   送出的 caseDetails：{case_details_str}", flush=True)
    except Exception as e:
        print(f"⚠️ [MachineC] Analysis({state}) 例外：{e} {_ts()}", flush=True)


def transfer_suggest(call_id: str, reason: str, case: dict | None = None) -> None:
    """送 NLP 建議轉接的提示給機器C，讓前端跳提示視窗。
    每通電話呼叫端負責去重，本函式不做去重。
    """
    case = case or {}
    payload = {
        "callId":       call_id,
        "reason":       reason,
        "caseTypeName": case.get("sub_category") or case.get("main_category") or "",
        "caseAddr":     case.get("address") or "",
        "caseSummary":  case.get("case_summary") or "",
        "callerPhone":  "",   # 119 case 無電話欄位（待從 Asterisk caller ID 取得）
        "timestamp":    datetime.now().isoformat(),
    }
    try:
        resp = requests.post(_TRANSFER_SUGGEST_URL, json=payload, timeout=MACHINEC_TIMEOUT)
        if resp.json().get("success"):
            print(f"💡 [MachineC] TransferSuggest({reason}) 送出 {_ts()}", flush=True)
        else:
            print(f"⚠️ [MachineC] TransferSuggest 回傳失敗：{resp.text} {_ts()}", flush=True)
    except Exception as e:
        print(f"⚠️ [MachineC] TransferSuggest 例外：{e} {_ts()}", flush=True)

def check_capacity(call_uuid: str = "") -> bool:
    """查機器C 是否還有空閒受理員（IdleLineCount）。

    回傳 True  = 滿線（IdleLineCount == 0，所有受理員忙碌 / 沒登入）
    回傳 False = 未滿線（正常進 AI 對話）

    行為：
      1. 環境變數 FORCE_MC_FULL=1 → 一律回 True（本地測試用）
      2. MC 回應數字 0 → True
      3. MC 回應數字 >0 → False
      4. MC API 呼叫失敗 → 保險起見回 False（讓通話正常進 AI，避免無故拒接）
    """
    # 本地測試 escape hatch
    if os.environ.get("FORCE_MC_FULL") == "1":
        print(f"🚫 [MachineC] FORCE_MC_FULL=1，模擬滿線 {_ts()}", flush=True)
        return True

    try:
        resp = requests.get(_IDLE_LINE_URL, timeout=MACHINEC_TIMEOUT)
        resp.raise_for_status()
        idle_count = int(resp.json())          # body 是 JSON scalar（純整數）
        if idle_count <= 0:
            print(f"🚫 [MachineC] IdleLineCount={idle_count}，滿線 [{call_uuid[:8]}] {_ts()}", flush=True)
            return True
        print(f"✅ [MachineC] IdleLineCount={idle_count}，未滿線 [{call_uuid[:8]}] {_ts()}", flush=True)
        return False
    except Exception as e:
        print(f"⚠️ [MachineC] IdleLineCount 查詢失敗、預設未滿線：{e} {_ts()}", flush=True)
        return False