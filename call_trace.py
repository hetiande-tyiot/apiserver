"""
call_trace.py — 回推一通電話裡，報案人每一句話之後受理員畫面上的判斷結果。

用法：
  apienv/bin/python call_trace.py 84e6575b          # 細項只列跟上一句比有變化的欄位
  apienv/bin/python call_trace.py 84e6575b --full   # 細項全部列出

資料來源（只讀，不寫資料庫）：
  utterances   — 報案人每一句話與時間
  analysis_log — 每次送給機器C 的 Analysis 完整 JSON

對應方式：某一句話的結果 = 從這句話開始、到下一句話之前，送給機器C 的最後一筆。
同一秒常有好幾筆，是 NLP 處理同一句話時分步驟推出的中間過程，這裡只取最後一筆。
限制：時間只到秒；若某一輪處理拖到下一句話之後才完成，那一筆會算到下一句。
"""
import argparse
import json
import sys

import db

# 每句報案人的話，連同它到下一句之前的時間範圍
_UTTERANCES_SQL = """
SELECT id, stt_text, created_at,
       LEAD(created_at) OVER (ORDER BY id) AS next_at
FROM utterances
WHERE uuid = %s AND role = 'caller'
ORDER BY id
"""


def _find_call(cur, prefix: str) -> str | None:
    """用通話編號前幾碼找出完整編號；對到多通時列出來請使用者輸入更長的編號。"""
    cur.execute(
        "SELECT uuid, created_at, main_category, sub_category FROM cases "
        "WHERE uuid LIKE %s ORDER BY created_at",
        (prefix + "%",),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"找不到通話編號開頭為 {prefix} 的電話。")
        return None
    if len(rows) > 1:
        print(f"通話編號開頭為 {prefix} 的電話有 {len(rows)} 通，請輸入更長的編號：")
        for r in rows:
            print(f"  {r['uuid']}  {r['created_at']}  {r['main_category']}/{r['sub_category']}")
        return None
    return rows[0]["uuid"]


def _details(payload: dict) -> dict:
    """caseDetails 在送給機器C 時是 JSON 字串，這裡轉回字典。"""
    try:
        return json.loads(payload.get("caseDetails") or "{}")
    except json.JSONDecodeError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="回推每一句話之後受理員畫面上的判斷結果")
    parser.add_argument("call", help="通話編號（至少前 8 碼）")
    parser.add_argument("--full", action="store_true", help="細項全部列出，不只列有變化的")
    args = parser.parse_args()

    conn = db.get_db()
    cur = conn.cursor()

    uuid = _find_call(cur, args.call.strip())
    if not uuid:
        return 1

    cur.execute("SELECT COUNT(*) AS n FROM analysis_log WHERE uuid = %s", (uuid,))
    if cur.fetchone()["n"] == 0:
        print(f"{uuid} 這通沒有 analysis_log 紀錄"
              "（2026-10-06 16:47 apiserver 重啟前的電話都沒有記錄送給機器C 的內容）。")
        return 1

    cur.execute(_UTTERANCES_SQL, (uuid,))
    utterances = cur.fetchall()
    cur.execute(
        "SELECT state, payload, created_at FROM analysis_log WHERE uuid = %s ORDER BY id",
        (uuid,),
    )
    logs = cur.fetchall()
    conn.close()

    print(f"通話 {uuid}：報案人 {len(utterances)} 句，送給機器C {len(logs)} 次\n")

    prev_details: dict = {}
    prev_top: dict = {}
    for i, u in enumerate(utterances, start=1):
        start, end = u["created_at"], u["next_at"]
        sends = [r for r in logs
                 if r["created_at"] >= start and (end is None or r["created_at"] < end)]
        print(f"【第 {i} 句】{start:%H:%M:%S} 報案人：{u['stt_text']}")
        if not sends:
            print("  （這句話之後沒有送出新內容給機器C，畫面維持上一句的狀態）\n")
            continue

        last = sends[-1]
        payload = json.loads(last["payload"])
        top = {
            "案類": payload.get("caseTypeName") or "",
            "地址": payload.get("caseAddr") or "",
            "摘要": payload.get("caseSummary") or "",
        }
        states = "、".join(r["state"] for r in sends)
        print(f"  送給機器C {len(sends)} 次（{states}）")
        for k, v in top.items():
            mark = "  ← 有變化" if prev_top and prev_top.get(k) != v else ""
            print(f"  {k}：{v or '（空）'}{mark}")

        details = _details(payload)
        if args.full:
            print("  細項：")
            for k, v in details.items():
                print(f"    {k}：{v}")
        else:
            changed = [(k, prev_details.get(k), details.get(k))
                       for k in list(prev_details) + [k for k in details if k not in prev_details]
                       if prev_details.get(k) != details.get(k)]
            if changed:
                print("  細項變化：")
                for k, old, new in changed:
                    if old is None:
                        print(f"    ＋ {k}：{new}")
                    elif new is None:
                        print(f"    － {k}：{old}（消失）")
                    else:
                        print(f"    ＊ {k}：{old} → {new}")
            else:
                print("  細項變化：無")
        print()
        prev_details, prev_top = details, top

    return 0


if __name__ == "__main__":
    sys.exit(main())
