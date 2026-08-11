# Asterisk AI 電話客服系統

這個 repo 同時保存了 **Asterisk PBX 設定檔** 與 **Python AI/STT 服務程式碼**，是一套讓來電進入 AI 對話、必要時轉真人客服並錄下雙方語音做即時 STT 的系統。

> Asterisk 與 Python daemon 實際在 Linux 機器（`/home/sipuser/`、`/etc/asterisk/`）上運行；本 repo 只是版本控制用的鏡像，路徑與實際部署位置不同。

---

## 專案目錄

```
poilcestation_asterisk/
├── ai/                              # Python 服務
│   ├── ami/amidaemon.py             # AMI Daemon（核心控制邏輯）
│   ├── audiosocket/
│   │   └── audiosocketv9000.py      # AudioSocket port 9000（AI 對話模式 + Silero VAD）
│   ├── ai_bridge_min.py             # ARI bridge 最小範例（測試用）
│   ├── download_silero_vad.py       # 下載並轉成 TorchScript 的 Silero VAD 模型
│   └── api/                         # 預留 API Server（尚未實作）
│
├── asterisk/etc/                    # Asterisk 設定檔（對應線上機 /etc/asterisk）
│   ├── extensions-ai.conf           # AI 對話 / 轉真人 / STT dialplan
│   ├── extensions-internal.conf     # 內線、ACD、出局路由等
│   ├── dialplan/                    # 另一份精簡 dialplan 子集
│   ├── pjsip*.conf, manager.conf, …  # PJSIP、AMI、ARI、CDR 等系統設定
│   └── （其他大量 .conf）
│
├── config/  scripts/  tests/        # 預留目錄（目前空的）
├── docs/README.md                   # 本文件
├── requirements.txt                 # 來源是線上機 pip freeze（含系統套件，非純專案依賴）
└── .gitignore
```

---

## 系統架構

```
來電
 └─► [Asterisk Dialplan]
       │
       │  Phase 1：AI 對話
       ├─► ai-service → ai-listen → AudioSocket(9000) → audiosocketv9000.py
       │     └─ Silero VAD 切段 → 存 WAV → TCP「END_SPEECH <uuid>」→ amidaemon (port 9900)
       │     └─ amidaemon 呼叫 AI Server → 取 TTS → 設 AMI 變數 → Redirect 到 ai-speak
       │     └─ ai-speak 播放 → AI_PLAY_DONE → 回 ai-listen
       │
       └─► Phase 2：轉真人（AI 判定需轉接時）
             ai-speak(transfer_agent) → caller-stt
               └─ amidaemon 收到 STT_CALL_START：
                    ├─ 建 caller FIFO → 啟動 fifo_stt_reader → MixMonitor caller
                    └─ Originate PJSIP/1006 → context=agent-stt
             agent-stt
               └─ amidaemon 收到 STT_AGENT_START：
                    ├─ 建 agent FIFO → 啟動 fifo_stt_reader → MixMonitor agent
                    └─ AMI Bridge caller + agent
             雙方通話中：caller / agent 各自 VAD 切段 → 存 WAV → 非同步 POST 到 STT Server
```

---

## 核心元件

| 檔案 | 角色 |
|------|------|
| [ai/ami/amidaemon.py](../ai/ami/amidaemon.py) | AMI Daemon。登入 Asterisk AMI、監聽 UserEvent、決策播放/轉接、起 MixMonitor、Bridge、跑 FIFO STT reader |
| [ai/audiosocket/audiosocketv9000.py](../ai/audiosocket/audiosocketv9000.py) | AudioSocket daemon (port 9000)。每連線一個 thread + 一個 Silero VAD model，切出語音段存 WAV，並 TCP 通知 amidaemon「END_SPEECH」 |
| [ai/download_silero_vad.py](../ai/download_silero_vad.py) | 一次性工具，下載 Silero VAD 並 `torch.jit` 序列化到 `/home/sipuser/models/silero_vad.jit` |
| [ai/ai_bridge_min.py](../ai/ai_bridge_min.py) | ARI bridge 最小範例（不在主線上，僅測試 ARI 流程） |
| [asterisk/etc/extensions-ai.conf](../asterisk/etc/extensions-ai.conf) | dialplan：`ai-service` / `ai-listen` / `ai-speak` / `ai-end` / `caller-stt` / `agent-stt` |
| [asterisk/etc/extensions-internal.conf](../asterisk/etc/extensions-internal.conf) | dialplan：內線、ACD（`4000` / `3000`）、出局路由 |

---

## 通話流程細節

### Phase 1：AI 對話
1. 進入 `ai-service`：`Wait(3)` → `Playback(welcome)` → 送 `AI_CALL_START` UserEvent → `Goto(ai-listen)`
2. `ai-listen`：送 `AI_LISTEN_START` → `AudioSocket(${MYUUID}, 127.0.0.1:9000)`
3. `audiosocketv9000.py`：以 UUID 建立 session 目錄、Silero VAD 偵測語音段落、存 WAV、TCP 送 `END_SPEECH <uuid>` 到 `127.0.0.1:9900`
4. `amidaemon.py` `speech_notify_server` 收到後：
   - 取最後一個 WAV → 重新命名為 `NNN.wav`
   - **目前是 TEST MODE**（[amidaemon.py:451](../ai/ami/amidaemon.py#L451)）：直接 `Setvar AI_NEXT=transfer` → `Redirect → ai-speak`
   - 正式模式（已註解）會呼叫 AI Server，取得 `executed` / `tts_path`，依 `AI_EXECUTED_MEANING` + `IVR_ACTION_POLICY` 決策播放
5. `ai-speak` 依 `AI_NEXT` / `AI_PLAY_TYPE` 分流：transfer / end / file / tts / silence；播完送 `AI_PLAY_DONE`，amidaemon redirect 回 `ai-listen`

### Phase 2：轉真人 + 雙方 STT
1. `ai-speak(transfer_agent)`：播 `tra-age` → `Goto(caller-stt,s,1)`
2. `caller-stt` 送 `STT_CALL_START` UserEvent → `Wait(300)`
3. amidaemon 收到：
   - `mkfifo /tmp/stt_caller_<uuid>.raw` → 開 thread 跑 `fifo_stt_reader(role="caller")`
   - 等 0.15s 後 `MixMonitor` 主檔 `_mixed.raw` + `options: r(<fifo>)`（只取 read 方向＝對方說話的音訊）
   - `Originate PJSIP/1006` → context=`agent-stt`
4. agent 接通進入 `agent-stt` → 送 `STT_AGENT_START` → amidaemon 同樣建 agent FIFO + reader + MixMonitor → `Action: Bridge` 把兩條 channel 串起來
5. `fifo_stt_reader` 持續 VAD 切段 → 存 WAV 到 `/home/sipuser/stt_bridge/<uuid>/<role>/` → 非同步 POST 到 STT Server

### 掛斷處理
- AMI `Hangup` 事件：若 caller 先掛、agent 還在響鈴中，amidaemon 會主動 `Action: Hangup` 切斷 pending agent channel，避免空響
- 同時清掉 `sessions[uuid]` 與 `last_end_speech_ts[uuid]`

---

## 端口與外部依賴

| Port | 方向 | 用途 |
|------|------|------|
| 5038 | Asterisk listens | AMI（`amiuser` / `Cde3xsw@`） |
| 9000 | audiosocketv9000.py listens | AudioSocket from Asterisk dialplan |
| 9900 | amidaemon listens | AudioSocket → AMI 的 `END_SPEECH` 通知 |
| 8088 | Asterisk listens | ARI（僅 `ai_bridge_min.py` 測試用） |

| 外部服務 | URL（hard-coded） |
|----------|-------------------|
| AI Server（對話決策） | `http://192.168.5.114:5000/audio/process_audio` |
| STT Server（語音辨識） | `http://192.168.5.130:4000/transcribe_breeze` |

---

## VAD 與切段參數

`audiosocketv9000.py`（AI 對話模式，模型每連線載一份）

```python
SILERO_THRESHOLD   = 0.5
SILENCE_TIMEOUT    = 1.2    # 靜音多久視為一句結束
PREBUFFER_DURATION = 0.5    # 語音前導保留秒數
TAIL_APPEND        = 0.8    # 語音尾音補充秒數
```

`amidaemon.py` 的 `fifo_stt_reader`（Bridge 雙方 STT，較嚴格）

```python
SILERO_THRESHOLD   = 0.65
SILENCE_TIMEOUT    = 1.2
PREBUFFER_DURATION = 0.5
TAIL_APPEND        = 0.8
# 段落 < 0.5 秒視為雜訊略過
```

---

## 啟動方式（部署機 `/home/sipuser/`）

```bash
# 1. dialplan 改完要 reload
asterisk -rx "dialplan reload"

# 2. AudioSocket daemon
python3 /home/sipuser/audiosocketv9000.py

# 3. AMI Daemon（另開一個 terminal）
python3 /home/sipuser/amidaemon.py
```

模型只要產過一次：

```bash
python3 /home/sipuser/download_silero_vad.py   # 產出 /home/sipuser/models/silero_vad.jit
```

---

## 線上機目錄

```
/home/sipuser/
├── amidaemon.py
├── audiosocketv9000.py
├── download_silero_vad.py
├── models/silero_vad.jit
├── audioraw/                 # AI 對話模式錄音：call-YYYYMMDD-<uuid8>/asr-HHMMSS-segN.wav
├── audiolog/                 # AudioSocket 事件 log：call-<uuid8>.log
├── stt_bridge/<uuid>/        # Bridge 雙方錄音：caller/ 與 agent/ 各自 segN.wav
└── ai_sessions/              # AI session 暫存

/etc/asterisk/                # 對應本 repo 的 asterisk/etc/
```

---

## 目前狀態與已知問題

### 已完成
- AI 對話：AudioSocket VAD → END_SPEECH → amidaemon 流程跑通
- 轉真人：originate PJSIP/1006 → bridge caller + agent
- caller 掛斷時自動切掉響鈴中的 agent channel
- 雙方獨立 MixMonitor + FIFO + VAD 切段存 WAV（舊流程，`amidaemon.py`）
- WAV 段落非同步送 STT Server（舊流程，`amidaemon.py`）
- **Cyberon STT WebSocket 串流整合**：[ai/ami/amidaemon_cyberon.py](../ai/ami/amidaemon_cyberon.py) 已實作並驗證啟動（連上 AMI、等待通話事件）；拿掉本地 Silero VAD，改由 server 端 EPD 切句，直接串流 PCM
- **Pylance 型別修復**（兩個檔案）：
  - `cyberon_stt_client.py`：`stop()` / `cancel()` 加 `_ws is None` 守衛；`_send_start()` / `_recv_loop()` 加 `assert self._ws is not None`；移除未使用的 `chunk_sec` 變數
  - `amidaemon_cyberon.py`：移除 `TYPE_CHECKING` 重複 import（與 importlib 賦值衝突）；移除未使用的 `STTResult`；`event.get("UserEvent")` 後加 `if not ue: continue` None 守衛；移除多餘的 `ue.strip()`（AMI 事件值在解析時已 strip）

### 待處理
- **TEST MODE 未拔**：[amidaemon.py:451](../ai/ami/amidaemon.py#L451) 收到任何 `END_SPEECH` 都固定 `AI_NEXT=transfer`，正式 AI Server 邏輯仍在註解裡
- **STT 直連 AI Server**：目前 `STT_SERVER_URL` 直打 `192.168.5.130:4000`，未來要改走 API Server 中轉並回吐字幕到前端
- **API Server 未實作**：[ai/api/](../ai/api/) 目前是空的
- **`requirements.txt` 不準**：內容是線上機 `pip freeze` 全集，含 cloud-init / ufw 等系統套件，缺實際用到的 `torch`、`numpy`、`requests`、`websocket-client`
- **dialplan 兩份**：`asterisk/etc/extensions-*.conf`（完整版）與 `asterisk/etc/dialplan/extensions_*.conf`（精簡版）並存，需釐清哪份是正在線上跑的
- **Cyberon STT 延遲調優**：real-time 測試發現辨識有延遲，EPD `epdFrameNum` 與 FIFO chunk 累積大小尚未最佳化（見 [audio-streaming-protocol.md §8](audio-streaming-protocol.md)）
- **逐字稿後送處**：STT partial / final 目前只 print，尚未接前端字幕或 log 儲存
