"""
payloads.py — FastAPI Pydantic 請求模型。
"""
from typing import Optional

from pydantic import BaseModel


class CallStartPayload(BaseModel):
    uuid: str
    channel: str
    transfer_mode: bool = False   # True = 轉接模式，不建立 NLP session
    dialed_number: str = ""       # 撥打的號碼（911 正式、912 SOP 調整測試環境），決定送哪一台 119 服務
    caller_number: str = ""       # 來電號碼（分機或外線號碼）


class SttPayload(BaseModel):
    uuid: str
    text: str
    role: str = "caller"
    timestamp: Optional[str] = None
    transfer: bool = False        # True = bridge 模式，走 /observe 而非 /input


class CallEndPayload(BaseModel):
    uuid: str


class TransferPayload(BaseModel):
    callId: str
    agentExtension: str
    reason: Optional[str] = "intervene"   # "intervene" | "reassign"
    timestamp: Optional[str] = None


class PrecheckPayload(BaseModel):
    uuid: str