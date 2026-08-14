#!/usr/bin/env python
"""
test_call.py — 模擬一通電話，驗證整條鏈路。

用法（一定要用虛擬環境的 python）：
    ./apienv/bin/python test_call.py                      # 預設劇本，走完整流程
    ./apienv/bin/python test_call.py --nlp-only           # 只測機器B，不碰 MC/MySQL/TTS
    ./apienv/bin/python test_call.py --addr "新北市板橋區中山路一段161號"
    ./apienv/bin/python test_call.py -t "我家失火了" -t "新北市板橋區文化路一段1號"

注意：
  - 不加 --nlp-only 時會實際寫 MySQL、產生 TTS 檔、推送給機器C。
  - 地址一定要帶「新北市」，否則機器B 的地址驗證會判 invalid。
"""
import argparse
import time
import uuid

import requests

from config import SOP_SERVER_URL

API = "http://127.0.0.1:8200"

# 預設劇本：救護 → 地址 → 確認 → 症狀
DEFAULT_TURNS = [
    "我要救護車",
    "{addr}",
    "對",
    "他是男生60歲，有呼吸但意識不清楚",
]
DEFAULT_ADDR = "新北市板橋區南雅南路2段152號4樓"


def run_full(turns: list[str], api: str) -> None:
    """走 api_server 完整流程：NLP + TTS + MySQL + 機器C 推送。"""
    call_id = str(uuid.uuid4())
    print(f"callId: {call_id}\n")

    pre = requests.post(f"{api}/call/precheck", json={"uuid": call_id}, timeout=15).json()
    print(f"precheck: 滿線={pre['is_full']}"
          f"{'   ⚠️ MC 滿線，推送會被拒收' if pre['is_full'] else ''}")

    r = requests.post(f"{api}/call/start",
                      json={"uuid": call_id, "channel": "PJSIP/test-0000fffe"},
                      timeout=60).json()
    print(f"開場白: {r['outputs']}")

    for text in turns:
        t0 = time.time()
        d = requests.post(f"{api}/call/stt",
                          json={"uuid": call_id, "text": text, "role": "caller"},
                          timeout=90).json()
        print(f"\n民眾: {text}   ({time.time() - t0:.1f}s)")
        print(f"  AI: {d['outputs']}")
        print(f"  done={d['done']} transfer={d['transfer']} audio={d['audio_path']}")
        if d["done"]:
            break

    e = requests.post(f"{api}/call/end", json={"uuid": call_id}, timeout=60).json()
    _dump_case(e.get("case") or {})
    print(f"\ncallId={call_id}")
    print(f"看推送結果： journalctl -u apiserver -n 60 | grep -E '{call_id[:8]}|MachineC'")


def run_nlp_only(turns: list[str]) -> None:
    """只打機器B，不碰 MC / MySQL / TTS。純測 NLP 行為時用這個。"""
    r = requests.post(f"{SOP_SERVER_URL}/session/new", timeout=30).json()
    sid = r["session_id"]
    print(f"session: {sid}")
    print(f"開場白: {r.get('outputs')}")

    for text in turns:
        t0 = time.time()
        d = requests.post(f"{SOP_SERVER_URL}/session/{sid}/input",
                          json={"text": text}, timeout=60).json()
        print(f"\n民眾: {text}   ({time.time() - t0:.1f}s)")
        print(f"  AI: {d.get('outputs')}  done={d.get('done')}")
        if d.get("done"):
            break

    case = requests.get(f"{SOP_SERVER_URL}/session/{sid}/result", timeout=15).json().get("case")
    _dump_case(case or {})
    requests.post(f"{SOP_SERVER_URL}/session/{sid}/hangup", timeout=15)


def _dump_case(case: dict) -> None:
    """印出案件裡有值的欄位（transcript 太長，只印筆數）。"""
    if not case:
        print("\n（沒有 case，通話可能未完成）")
        return
    print("\n案件欄位：")
    for k, v in case.items():
        if v in (None, "", False, []):
            continue
        if k == "transcript":
            print(f"  {k:28} = <{len(v)} 筆對話>")
        else:
            print(f"  {k:28} = {v}")


def main() -> None:
    p = argparse.ArgumentParser(description="模擬一通 119 電話")
    p.add_argument("--nlp-only", action="store_true",
                   help="只測機器B，不寫 MySQL、不推 MC")
    p.add_argument("--addr", default=DEFAULT_ADDR,
                   help=f"事發地址（記得帶新北市）。預設：{DEFAULT_ADDR}")
    p.add_argument("-t", "--turn", action="append", dest="turns",
                   help="自訂民眾說的每一句，可重複給。給了就不用預設劇本")
    p.add_argument("--api", default=API, help=f"api_server 位址，預設 {API}")
    args = p.parse_args()

    turns = args.turns or [t.format(addr=args.addr) for t in DEFAULT_TURNS]
#!/usr/bin/env python
"""
test_call.py — 模擬一通電話，驗證整條鏈路。

用法（一定要用虛擬環境的 python）：
    ./apienv/bin/python test_call.py                      # 預設劇本，走完整流程
    ./apienv/bin/python test_call.py --nlp-only           # 只測機器B，不碰 MC/MySQL/TTS
    ./apienv/bin/python test_call.py --addr "新北市板橋區中山路一段161號"
    ./apienv/bin/python test_call.py -t "我家失火了" -t "新北市板橋區文化路一段1號"
    ./apienv/bin/python test_call.py -i                   # 互動模式，自己邊打邊對話
    ./apienv/bin/python test_call.py -i --nlp-only        # 互動 + 只測機器B

注意：
  - 不加 --nlp-only 時會實際寫 MySQL、產生 TTS 檔、推送給機器C。
  - 地址一定要帶「新北市」，否則機器B 的地址驗證會判 invalid。
  - -i 會忽略 -t / --addr，每一句都由你即時輸入。
"""
import argparse
import time
import uuid

import requests

from config import SOP_SERVER_URL

API = "http://127.0.0.1:8200"

# 預設劇本：救護 → 地址 → 確認 → 症狀
DEFAULT_TURNS = [
    "火災",
    "{addr}",
    "對",
    "我吃飯到一半餐廳燒起來",
    "感覺越來越大",
    "我原本在裡面吃飯啦 就趕快跑出來了 然後感覺快燒到旁邊了",
    "裡面現在沒有人",
    "共5樓 現在是1樓在燒",
]
DEFAULT_ADDR = "新北市板橋區南雅南路2段152號4樓"


def _iter_turns(turns: list[str] | None):
    """turns 有值就照劇本跑；為 None 則進互動模式，逐句從 stdin 讀。

    互動模式下空行或 Ctrl-D 結束通話，收尾流程照樣會跑完。
    """
    if turns is not None:
        yield from turns
        return

    print("互動模式：輸入民眾說的話，按 Enter 送出。空行或 Ctrl-D 結束通話。\n")
    while True:
        try:
            text = input("民眾> ").strip()
        except EOFError:
            print()
            return
        if not text:
            return
        yield text


def run_full(turns: list[str] | None, api: str) -> None:
    """走 api_server 完整流程：NLP + TTS + MySQL + 機器C 推送。"""
    call_id = str(uuid.uuid4())
    print(f"callId: {call_id}\n")

    pre = requests.post(f"{api}/call/precheck", json={"uuid": call_id}, timeout=15).json()
    print(f"precheck: 滿線={pre['is_full']}"
          f"{'   ⚠️ MC 滿線，推送會被拒收' if pre['is_full'] else ''}")

    r = requests.post(f"{api}/call/start",
                      json={"uuid": call_id, "channel": "PJSIP/test-0000fffe"},
                      timeout=60).json()
    print(f"開場白: {r['outputs']}")

    try:
        for text in _iter_turns(turns):
            t0 = time.time()
            d = requests.post(f"{api}/call/stt",
                              json={"uuid": call_id, "text": text, "role": "caller"},
                              timeout=90).json()
            elapsed = time.time() - t0
            # 互動模式下這句是使用者自己打的，不用再回顯一次
            print(f"\n民眾: {text}   ({elapsed:.1f}s)" if turns is not None
                  else f"  ({elapsed:.1f}s)")
            print(f"  AI: {d['outputs']}")
            print(f"  done={d['done']} transfer={d['transfer']} audio={d['audio_path']}")
            if d["done"]:
                break
    except KeyboardInterrupt:
        # 中斷也要走完 /call/end，否則 apiserver 的 session 會留著不放
        print("\n（已中斷，仍會正常結束通話）")

    e = requests.post(f"{api}/call/end", json={"uuid": call_id}, timeout=60).json()
    _dump_case(e.get("case") or {})
    print(f"\ncallId={call_id}")
    print(f"看推送結果： journalctl -u apiserver -n 60 | grep -E '{call_id[:8]}|MachineC'")


def run_nlp_only(turns: list[str] | None) -> None:
    """只打機器B，不碰 MC / MySQL / TTS。純測 NLP 行為時用這個。"""
    r = requests.post(f"{SOP_SERVER_URL}/session/new", timeout=30).json()
    sid = r["session_id"]
    print(f"session: {sid}")
    print(f"開場白: {r.get('outputs')}")

    try:
        for text in _iter_turns(turns):
            t0 = time.time()
            d = requests.post(f"{SOP_SERVER_URL}/session/{sid}/input",
                              json={"text": text}, timeout=60).json()
            elapsed = time.time() - t0
            print(f"\n民眾: {text}   ({elapsed:.1f}s)" if turns is not None
                  else f"  ({elapsed:.1f}s)")
            print(f"  AI: {d.get('outputs')}  done={d.get('done')}")
            if d.get("done"):
                break
    except KeyboardInterrupt:
        # 中斷也要 hangup，否則機器B 的 session 會卡著
        print("\n（已中斷，仍會正常結束 session）")

    case = requests.get(f"{SOP_SERVER_URL}/session/{sid}/result", timeout=15).json().get("case")
    _dump_case(case or {})
    requests.post(f"{SOP_SERVER_URL}/session/{sid}/hangup", timeout=15)


def _dump_case(case: dict) -> None:
    """印出案件裡有值的欄位（transcript 太長，只印筆數）。"""
    if not case:
        print("\n（沒有 case，通話可能未完成）")
        return
    print("\n案件欄位：")
    for k, v in case.items():
        if v in (None, "", False, []):
            continue
        if k == "transcript":
            print(f"  {k:28} = <{len(v)} 筆對話>")
        else:
            print(f"  {k:28} = {v}")


def main() -> None:
    p = argparse.ArgumentParser(description="模擬一通 119 電話")
    p.add_argument("--nlp-only", action="store_true",
                   help="只測機器B，不寫 MySQL、不推 MC")
    p.add_argument("--addr", default=DEFAULT_ADDR,
                   help=f"事發地址（記得帶新北市）。預設：{DEFAULT_ADDR}")
    p.add_argument("-t", "--turn", action="append", dest="turns",
                   help="自訂民眾說的每一句，可重複給。給了就不用預設劇本")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="互動模式：每一句自己即時輸入（會忽略 -t / --addr）")
    p.add_argument("--api", default=API, help=f"api_server 位址，預設 {API}")
    args = p.parse_args()

    # None 代表互動模式，交給 _iter_turns 從 stdin 讀
    turns = None if args.interactive else (
        args.turns or [t.format(addr=args.addr) for t in DEFAULT_TURNS])

    if args.nlp_only:
        run_nlp_only(turns)
    else:
        run_full(turns, args.api)


if __name__ == "__main__":
    main()

    if args.nlp_only:
        run_nlp_only(turns)
    else:
        run_full(turns, args.api)


if __name__ == "__main__":
    main()
