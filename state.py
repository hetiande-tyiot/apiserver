"""
state.py — api_server 程序內共用狀態。

包含三類：
  1. sop_sessions / turn_counters：原本 110LLM session 對應 + TTS 輪次
  2. mc_state：MC 推送相關的去重旗標（從 amidaemon 搬過來）
"""
import threading
from dataclasses import dataclass, field


# ── LLM 端 ────────────────────────────────────────────────────
sop_sessions: dict[str, str] = {}   # uuid → sop_session_id
turn_counters: dict[str, int] = {}  # uuid → 目前 TTS 輪次


# ── MC 推送去重狀態 ──────────────────────────────────────────────
@dataclass
class MCSessionState:
    """每通電話對 MC 推送的去重旗標。"""
    last_sent_case: dict | None = None     # 上次送 Analysis 的 case summary（dedup 用）
    transfer_suggested: bool = False       # 已送過 TransferSuggest
    final_sent: bool = False               # 已送過 Analysis(Final)
    should_end: bool = False               # SSE done 已到、通話該結束
    intervened: bool = False               # 受理員按過介入（hangup 110LLM 後 /observe 才可用）
    sse_stop_event: threading.Event | None = None   # SSE consumer 停止訊號


mc_state: dict[str, MCSessionState] = {}   # uuid → MCSessionState
