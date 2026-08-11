"""
db.py — MySQL (fire119) 連線與所有 INSERT/UPDATE 封裝。

資料表：
  cases      — 一通電話一筆案件
  utterances — 逐句 STT 紀錄
  dialogue   — 一輪 AI 對話（stt_text + nlp_reply + audio_path）
"""
import json
from datetime import datetime

import pymysql

from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB


def get_db():
    return pymysql.connect(
        host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS, database=MYSQL_DB,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )


def insert_case(uuid: str, channel: str) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO cases (uuid, channel, created_at) VALUES (%s, %s, %s)",
                (uuid, channel, datetime.now()),
            )
        db.close()
    except Exception as e:
        print(f"⚠️  MySQL cases insert 失敗：{e}", flush=True)


def insert_utterance(uuid: str, role: str, text: str, ts: str) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO utterances (uuid, role, stt_text, created_at) VALUES (%s,%s,%s,%s)",
                (uuid, role, text, ts),
            )
        db.close()
    except Exception as e:
        print(f"⚠️  MySQL utterance insert 失敗：{e}", flush=True)


def insert_dialogue(uuid: str, turn: int, stt_text: str, nlp_reply: str, audio_path: str) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO dialogue (uuid, turn, stt_text, nlp_reply, audio_path, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (uuid, turn, stt_text, nlp_reply, audio_path, datetime.now()),
            )
        db.close()
    except Exception as e:
        print(f"⚠️  MySQL dialogue insert 失敗：{e}", flush=True)


def update_nlp(uuid: str, nlp_reply: str) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE cases SET nlp_reply = %s, updated_at = %s WHERE uuid = %s",
                (nlp_reply, datetime.now(), uuid),
            )
        db.close()
    except Exception as e:
        print(f"⚠️  MySQL nlp_reply 更新失敗：{e}", flush=True)


def save_case(uuid: str, case: dict) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """UPDATE cases SET main_category=%s, sub_category=%s, address=%s,
                   is_ohca=%s, call_result=%s, case_summary=%s,
                   case_json=%s, updated_at=%s WHERE uuid=%s""",
                (
                    case.get("main_category"),
                    case.get("sub_category"),
                    case.get("address"),
                    case.get("is_ohca"),
                    case.get("result"),
                    case.get("case_summary"),
                    json.dumps(case, ensure_ascii=False),
                    datetime.now(), uuid,
                ),
            )
        db.close()
        print(f"✅ [{uuid[:8]}] 案件存入 MySQL（{case.get('main_category')}/{case.get('sub_category')}）", flush=True)
    except Exception as e:
        print(f"⚠️  MySQL case_json 存入失敗：{e}", flush=True)


def update_case_ended(uuid: str) -> None:
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE cases SET ended_at = %s WHERE uuid = %s",
                (datetime.now(), uuid),
            )
        db.close()
    except Exception as e:
        print(f"⚠️  MySQL ended_at 更新失敗：{e}", flush=True)
