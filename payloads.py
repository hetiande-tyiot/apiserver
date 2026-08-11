"""
payloads.py — FastAPI Pydantic 請求模型。
"""
from typing import Optional

from pydantic import BaseModel


class CallStartPayload(BaseModel):
    uuid: str
    channel: str
    transfer_mode: bool = False   # True = 轉接模式，不建立 NLP session


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