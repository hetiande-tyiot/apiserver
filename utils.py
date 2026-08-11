"""utils.py — 共用小工具。"""
from datetime import datetime


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")
