# amidaemon 專案筆記

_最後更新：2026-05-17_

---

## 概述

amidaemon 是機器A 的 AMI 監聽程式，負責**電話控制層**：

- 透過 AMI 與 Asterisk 溝通
- 從 FIFO 讀取 PCM 音訊，用 Silero VAD 斷句後送 Cyberon STT
- 將 STT 文字推給本機 api_server，取回 NLP 回覆 + TTS 路徑
- 觸發 Asterisk Playback() 播放 TTS
- 控制轉接受理員（含 CTI 相容、機器C 介入指令）

**邊界**：amidaemon **不直接**對接機器B 與機器C 的業務 API（NLP、TTS、Conversation、Analysis），這些統一走 api_server。amidaemon 只關心：AMI、FIFO、Cyberon STT、推給 api_server、收到機器C 介入指令。

---

## 啟動 / 停止

```bash
# 啟動
python3 /home/sipuser/policestation_project/ai/ami/amidaemon_cyberon.py

# 停止
pkill -f amidaemon_cyberon.py
```

啟動時會看到：
```
🧠 載入 Silero VAD 模型...
✅ Silero VAD 就緒
✅ AMI 登入成功
🔊 Cyberon STT URL: ws://192.168.5.204:8890/SttProxy/recognition HH:MM:SS
🛰️  介入指令 HTTP 端點啟動：http://127.0.0.1:8201/transfer
```

依賴套件：`silero-vad`, `numpy`, `torch`, `requests`, `websockets`（Cyberon STT client 用）。

---

## 模組結構

`/home/sipuser/policestation_project/ai/ami/`

| 檔案 | 行數 | 職責 |
|------|------|------|
| `amidaemon_cyberon.py` | ~245 | 主程式：AMI 事件迴圈、Session 建立、轉接判斷 |
| `config.py` | ~55 | 所有可調參數集中區 |
| `session.py` | ~30 | Session 物件 + 共用 `sessions` dict |
| `vad.py` | ~40 | Silero VAD 封裝（**換 VAD 模型只改這支**） |
| `stt_reader.py` | ~200 | FIFO + VAD + Cyberon STT 斷句（**換 STT 引擎只改這支**） |
| `ami_actions.py` | ~80 | AMI 工具：send/login/redirect/setvar/mixmonitor/transfer |
| `api_client.py` | ~140 | 呼叫 api_server + 推送 Machine C Conversation/Analysis |
| `machinec_client.py` | ~45 | 機器C HTTP 呼叫 |
| `intervention_server.py` | ~70 | 內部 HTTP（:8201）接收介入指令 |
| `utils.py` | ~5 | `_ts()` 時間戳記 |

---

## 設定參數（`config.py`）

| 參數 | 預設 | 說明 |
|------|------|------|
| `AMI_HOST/PORT/USER/PASS` | `127.0.0.1:5038` | Asterisk AMI 連線 |
| `API_SERVER_URL` | `http://127.0.0.1:8200` | 本機 api_server |
| `CYBERON_URL` | `ws://192.168.5.204:8890/...` | 機器B STT WebSocket |
| `CYBERON_TOKEN` | （內含） | STT 授權 token |
| `PCM_CHUNK_BYTES` | 800 | 每次從 FIFO 讀的 PCM 大小（50ms） |
| `SILERO_THRESHOLD` | 0.7 | VAD 語音機率閾值（越高越不靈敏） |
| `STT_VAD_SILENCE_MS` | 800 | 靜音達此值送 stop（歷程：800→200→500→800） |
| `TRANSFER_AGENT_EXT` | `"1006"` | 預設轉接分機（fallback） |
| `MACHINEC_URL` | `http://61.216.64.19:8055` | 機器C 前端 |
| `INTERVENTION_HOST/PORT` | `127.0.0.1:8201` | 內部介入 HTTP 端點 |
| `ENABLE_DUAL_CHANNEL_STT` | `False` | 非 CTI 場景測試開關 |

---

## 核心流程

### A. AI 對話階段

```
[來電撥 4000]
  Asterisk ai-call → ai-service → UserEvent(AI_CALL_START)
  → amidaemon 建 Session、Redirect → caller-stt
  → UserEvent(STT_CALL_START)
  → amidaemon 建 FIFO、啟 fifo_stt_reader、AMI MixMonitor
  → 非同步 POST api_server /call/start（取開場白 TTS）
  → 收到 audio_path → AMI Setvar + Redirect → ai-speak
  → Asterisk Playback() → UserEvent(AI_PLAY_DONE)
  → amidaemon Redirect → caller-stt

[每句 STT]
  Silero VAD 偵測靜音 800ms → 送 stop → 合併 FINAL → 送 api_server /call/stt
  → api_server 回 audio_path → Redirect → ai-speak → Playback → AI_PLAY_DONE → caller-stt
  ↑ 重複直到 NLP 回 done=true 或機器C 介入
```

### B. 轉接受理員（兩種情境）

**情境一：NLP 自動觸發**
```
api_server /call/stt 回 transfer=true
  → session.transfer_mode = True
  → session.pending_agent_ext = "1006"（目前固定，待改機器C 給的動態號碼）
  → 若有 TTS 待播 → 等 AI_PLAY_DONE 才轉接（避免兩個 Redirect 競態）
  → 若無 TTS → 立即 Redirect caller → ai-transfer-dial
```

**情境二：機器C 介入按鈕**
```
機器C → api_server /api/AiRobot/Transfer
  → api_server forward → amidaemon :8201/transfer
  → amidaemon 重置 agent_channel, 設新 pending_agent_ext
  → ami_transfer_to_agent → Redirect caller → ai-transfer-dial
    （Redirect 會自動打斷正在播的 TTS 與舊 Dial）
```

### C. Bridge 後雙通道 STT

```
ChannelStateChange Up for PJSIP/1006-xxxx
  → setup_agent_stt() 建 agent FIFO、啟 fifo_stt_reader（role="agent"）
  → AMI MixMonitor on PJSIP/1006-xxxx
  → caller / agent 各自 STT 都送 api_server /call/stt (transfer=True)
  → api_server 內部 POST 機器B /observe（caller 觸發 LLM 抽取，agent 只記 transcript）
```

---

## AMI 事件處理

| AMI 事件 | 動作 |
|---|---|
| `UserEvent(AI_CALL_START)` | 建立 Session |
| `UserEvent(STT_CALL_START)` | 建 FIFO + 啟 STT reader + 呼叫 api_server /call/start |
| `UserEvent(AI_PLAY_DONE)` | TTS 播完 → 回 caller-stt 或執行延後的轉接 |
| `UserEvent(TRANSFER_TEST_START)` | 撥 4001 直接測試轉接（跳過 AI 對話） |
| `Newchannel` | 追蹤 agent channel 名稱（響鈴中） |
| `ChannelStateChange Up` | Agent 接通 → 啟動 agent STT/MixMonitor |
| `Hangup` | 清理 Session、通知 api_server /call/end |

---

## 重要設計決策

### 1. CTI 相容轉接（**不可變動 cti.conf**）

轉接**必須**用 `Redirect caller → ai-transfer-dial → Dial(PJSIP/分機)`，**不可**用 `Originate`。
原因：CTI 的 Answer 按鈕只認 Dial() 的 B-leg，孤立 originate 會讓應答無效。

### 2. Silero VAD 執行緒安全

每個 `fifo_stt_reader` 用 `copy.deepcopy(_get_silero_vad())` 取得獨立模型副本，
避免 caller / agent thread 共用 LSTM 狀態互相干擾。

### 3. NLP 競態保護（`nlp_pending`）

VAD 觸發送 NLP 後到 NLP 回覆前約 1s，期間使用者繼續說話的 STT 會被忽略，
避免 NLP 重複呼叫與回覆錯位。HTTP response 收到（成功或失敗）才解除。

### 4. TTS 播放期間忽略 STT

`session.state == STATE_SPEAKING` 時 VAD 重置、不觸發 stop，避免擴音回音被當成新句子。

### 5. 轉接 vs TTS 競態（延後轉接）

NLP 同時回傳「TTS + transfer=True」時，先播完 TTS（提示音「正為您轉接...」），
等 `UserEvent(AI_PLAY_DONE)` 後才執行 Redirect。否則兩個 Redirect 會打架。

### 6. 介入端點分離

`intervention_server.py` 開在 `127.0.0.1:8201`，由 api_server 內部 forward。
日後 api/ami 機器拆分時，把 `INTERVENTION_HOST` 改成內網 IP 即可。

---

## Log 格式

所有 print 結尾加 `_ts()` 產生的時間戳：

```
📞 通話開始：4975a464-... 11:11:39
📂 FIFO STT reader 啟動: [caller] /tmp/stt_caller_xxx.raw 11:11:39
🔇 [4975a464] VAD 靜音 800ms，送 stop 11:11:47
📝 STT [caller] [FINAL  ] 你好，我要報案。 11:11:48
📤 [4975a464] 送 NLP：你好，我要報案。 11:11:51
🤖 [4975a464] 受理員（針對：你好，我要報案。）：請問需要哪類協助？ 11:12:11
🔊 [4975a464] 觸發 TTS 播放：/home/sipuser/tts_audio/4975a464_2 11:12:11
🔀 Redirect → ai-speak (PJSIP/1003-xxx) 11:12:11
🎯 [Intervene/intervene] 4975a464 → PJSIP/1006 11:12:30
🧹 Hangup cleanup：4975a464-... 11:12:50
```

特殊 log：
- `⏭️ NLP處理中，略過：...` — `nlp_pending` 阻擋
- `⏭️ TTS播放中，略過：...` — `STATE_SPEAKING` 阻擋
- `ℹ️ NLP 回空白（針對：...）` — 110LLM 沒產生回覆

---

## 已知問題 / TODO

- [ ] 轉接分機改用機器C `/AssignAgent` 給的動態號碼（目前 NLP 觸發走預設 1006）
- [ ] 滿線機制：`/AssignAgent` 回 null 時播「目前線路忙碌」並掛斷
- [ ] VAD 斷句最佳化：目前 800ms 仍偶有句中截斷；可考慮 AudioSocket 或 Push-to-talk
- [ ] STT 整體延遲約 3–4 秒（MixMonitor buffer ~1s 是大宗），AudioSocket 可消除此延遲

---

## 相關文件

- 整體架構與機器B/C 連動：`docs/localprogress/project-notes.md`
- 機器C 對接 API 規格：`docs/talk_toC/transfer-api-spec.md`
- amidaemon 程式碼詳細報告：`docs/localprogress/amidaemon-code-report.md`
