"""
config.example.py — config.py 的範本，進版控用。

config.py 本身不進版控（每台機器不同：71 公司開發 / 34 局內 / 119 正式機），
部署新機器時：

    cp config.example.py config.py

然後把下面標了 ← 改我 的地方填成該台機器的實際值。
所有參數都可以再用環境變數覆寫（systemd unit 裡設 Environment= 即可），
填在這裡的只是「沒設環境變數時的預設值」。
"""
import os

# ================================================================
# 機器B：119LLM
# ================================================================
SOP_SERVER_URL = os.environ.get("SOP_SERVER_URL", "http://<機器B-IP>:8200")   # ← 改我

# ================================================================
# amidaemon 內部控制端點（介入轉接 + 通話結束指令）
# 日後 api 與 ami 機器拆分時，把 host 改成內網 IP
# ================================================================
AMIDAEMON_URL = os.environ.get("AMIDAEMON_URL", "http://127.0.0.1:8201")

# ================================================================
# 機器C（前端系統）
# ================================================================
MACHINEC_URL = os.environ.get("MACHINEC_URL", "http://<機器C-IP>:8050")        # ← 改我
MACHINEC_TIMEOUT = int(os.environ.get("MACHINEC_TIMEOUT", "2"))
MACHINEC_TOKEN = os.environ.get("MACHINEC_TOKEN", "<向機器C 索取>")            # ← 改我
#   送出時的形式：Authorization: Bearer <token>

# ================================================================
# MySQL（fire119）
# ================================================================
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_USER = os.environ.get("MYSQL_USER", "<db 帳號>")                         # ← 改我
MYSQL_PASS = os.environ.get("MYSQL_PASS", "<db 密碼>")                         # ← 改我
MYSQL_DB = os.environ.get("MYSQL_DB",   "fire119")
#   建表 SQL 見 schema_119.sql

# ================================================================
# TTS gRPC（Cyberon 協定）
# 兩種 backend 各自獨立：用 TTS_BACKEND 切換，各自的參數在 TTS_BACKENDS 裡分開設定。
#   "cyberon" : 賽微 Cyberon TTS
#   "f5"      : F5-TTS Adapter（機器B）
# 每個參數都可用環境變數覆寫：TTS_BACKEND、TTS_CYBERON_HOST、TTS_F5_SPEED ...
#
# speed: 0.5(慢) ~ 2.0(快)，1.0=正常；F5 建議 0.8~1.3 最自然、超出區間 B 端 clamp
# gain : 0.5(小) ~ 4.0(大)，1.0=原音；F5 端會削波保護、建議 ≤ 2.0
#   實測：speed 0.7→7.97s / 1.0→5.57s / 1.5→3.71s（同一句）；gain 2.0 → RMS 翻倍
#   （見 docs/shared_with_B/tts-speed-gain-for-machineA.md）
# 其他已知 port：CosyVoice3 8090
# ================================================================
TTS_BACKEND = os.environ.get("TTS_BACKEND", "f5")   # ← 改我：cyberon / f5

TTS_BACKENDS = {
    "cyberon": {
        "host":    os.environ.get("TTS_CYBERON_HOST", "<Cyberon-IP>"),   # ← 改我
        "port":    int(os.environ.get("TTS_CYBERON_PORT", "8088")),
        "speaker": os.environ.get("TTS_CYBERON_SPEAKER", "<可用語者，例如 Sharon>"),   # ← 改我
        "speed":   float(os.environ.get("TTS_CYBERON_SPEED", "1.0")),
        "gain":    float(os.environ.get("TTS_CYBERON_GAIN", "0.8")),
        "token":   os.environ.get("TTS_CYBERON_TOKEN", "<Cyberon token>"),   # ← 改我
    },
    "f5": {
        "host":    os.environ.get("TTS_F5_HOST", "<機器B-IP>"),   # ← 改我
        "port":    int(os.environ.get("TTS_F5_PORT", "8089")),
        "speaker": os.environ.get("TTS_F5_SPEAKER", "Yain2"),
        "speed":   float(os.environ.get("TTS_F5_SPEED", "1.0")),
        "gain":    float(os.environ.get("TTS_F5_GAIN", "0.8")),
        "token":   os.environ.get("TTS_F5_TOKEN", "<向機器B 索取>"),   # ← 改我
    },
}
TTS_AUDIO_DIR = os.environ.get("TTS_AUDIO_DIR", os.path.expanduser("~/tts_audio"))
os.makedirs(TTS_AUDIO_DIR, exist_ok=True)

# ================================================================
# HTTP timeout（呼叫 119LLM / amidaemon）
# ================================================================
HTTP_TIMEOUT = 10
