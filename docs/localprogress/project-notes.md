# 專案筆記（機器A進度與架構）

_最後更新：2026-05-17_

---

## 拓撲

```
機器A（192.168.5.x）          機器B（192.168.5.204）         機器C（前端 61.216.64.19:8055）
─────────────────────          ──────────────────────         ─────────────────────────
Asterisk PBX                   Cyberon STT  :8890 (WS)        受理員介面
amidaemon (含介入HTTP :8201)   Cyberon TTS  :8088 (gRPC)      Conversation 顯示
api_server          :8200      sop_api_server :8100           Analysis 顯示
MySQL police110                (FastAPI/110LLM)               介入按鈕
/home/sipuser/apiserver/
/home/sipuser/tts_audio/
CTI（本機，不可動）

對外連線：
  機器A → 機器B：STT/TTS/110LLM
  機器A → 機器C：POST Conversation, POST Analysis（推送對話與案件分析）
  機器C → 機器A：POST /api/AiRobot/Transfer（介入/重新派工指令）
```

---

## 完整通話流程（AI 對話階段）

```
報警人來電 → 撥 4000
  → Asterisk ai-call → ai-service
  → Wait(3) + UserEvent(AI_CALL_START)
  → Goto(caller-stt)

[STT_CALL_START 事件]
  → amidaemon 建 FIFO + 啟 CyberonSTTClient + MixMonitor
  → amidaemon POST /call/start → api_server
      ├── MySQL INSERT cases
      ├── 110LLM /session/new → 開場白文字
      └── Cyberon TTS (gRPC) → 8kHz WAV
  → amidaemon AMI Setvar + Redirect → ai-speak
  → Asterisk Playback(開場白WAV) → 報警人聽到
  → UserEvent(AI_PLAY_DONE)
  → amidaemon Redirect → caller-stt（繼續監聽）

[每句 STT]
  webrtcvad 逐幀偵測語音活動：
    - 有語音 → speech_started=True，累積 server EPD 送來的 FINAL 至 final_buffer
    - 靜音達 800ms → 送 stop 給 STT server
    - stop 後等 server flush 最後片段 → 合併 final_buffer → 送 NLP
  → amidaemon POST /call/stt → api_server
      ├── MySQL INSERT utterances
      ├── 110LLM /session/{id}/input → NLP 回覆文字
      ├── Cyberon TTS (gRPC, 16kHz→8kHz 降採樣) → WAV
      ├── MySQL INSERT dialogue（stt_text + nlp_reply + audio_path）
      └── MySQL UPDATE cases (nlp_reply)
  → api_server 回傳 audio_path
  → amidaemon AMI Setvar + Redirect → ai-speak
  → Asterisk Playback(WAV) → 報警人聽到
  → UserEvent(AI_PLAY_DONE)
  → amidaemon Redirect → caller-stt（繼續監聽）
  ↑ 重複直到 110LLM done=true + error="轉接專人"
```

---

## 轉接真人流程（Bridge 後雙通道 NLP）

```
[110LLM 決定轉接]
  → /call/stt 回傳 done=true, error="轉接專人", transfer=true
  → amidaemon 收到後：
      session.transfer_mode = True
      session.pending_agent_ext = "1006"
      AMI Setvar: TRANSFER_TARGET=1006
      AMI Redirect caller → [ai-transfer-dial] → Dial(PJSIP/1006,60,Tt)

[1006 響鈴（正常 Dial B-leg）]
  → Newchannel(PJSIP/1006-xxxx) → session.pending_agent_channel = "PJSIP/1006-xxxx"
  → CTI 看到 1006 響鈴 → 受理員點「應答」
  → CTI 對 PJSIP/1006-xxxx 送 Redirect → cti-answer → Answer()+Wait(3600)
  → Dial() 偵測 B-leg answered → Caller ↔ 1006 橋接

[ChannelStateChange Up for PJSIP/1006-xxxx]
  → amidaemon _setup_agent_stt():
      建 agent FIFO
      啟 fifo_stt_reader（role="agent"）
      AMI MixMonitor r(agent_fifo) on PJSIP/1006-xxxx
  （Caller 的 MixMonitor+STT 持續運行，無需重啟）

[Bridge 後 STT → /observe（雙通道 NLP）]
  caller STT FINAL → POST /call/stt {transfer:true} → api_server → POST /observe {role:"caller"}
    → 機器B LLM 抽取欄位（upgrade mode），更新 case_summary
  agent STT FINAL → POST /call/stt {transfer:true, role:"agent"} → api_server → POST /observe {role:"agent"}
    → 只 append transcript，不觸發 LLM

[通話結束]
  → POST /call/end → 110LLM /hangup → GET /result → MySQL UPDATE cases（含 bridge 後更新）
```

---

## 測試用入口（4001 直接測試轉真人）

```
撥 4001 → [internal] Goto(ai-transfer-test,s,1)
  → Answer + Wait(1) + Set MYUUID
  → UserEvent(TRANSFER_TEST_START)
  → amidaemon:
      - 建立 session（transfer_mode=True）
      - 設 caller MixMonitor + caller STT
      - POST /call/start（transfer_mode=True，建 cases 但不啟 110LLM）
      - AMI Redirect caller → ai-transfer-dial → Dial(PJSIP/1006,60,Tt)
```

觸發關鍵字測試（AI 對話中說「測試轉接」）：
- api_server 的 `TEST_TRANSFER_KEYWORD = "測試轉接"` 匹配到時直接回 transfer=True
- 正式上線時移除此邏輯

---

## CTI 相容性設計（重要）

**問題背景（已解決）**：
最初用 `AMI Originate: Channel: PJSIP/1006, Context: agent-stt`，CTI 的應答按鈕無效。

根本原因分析：
1. 我們直接 originate 到 PJSIP/1006 → 1006 是「孤立的 standalone originate channel」
2. CTI 看到 1006 響鈴，按應答時對 PJSIP/1006-xxxx 做 Redirect → cti-answer
3. `cti-answer` = `Answer() + Wait(3600)`，但沒有對應的 A-leg Dial()，Caller 無法 bridge
4. 部分情況下 CTI 甚至會另外自建 PJSIP/1006-xxxx 新通道，走自己的 internal/1006 flow

**解決方案**：
改用 `AMI Redirect caller → [ai-transfer-dial]`，讓 caller 自己做 `Dial(PJSIP/1006,60,Tt)`：
- 1006 成為 Dial() 的正常 B-leg → CTI 識別為標準來電
- CTI 對 1006 送 `cti-answer` → Dial() 自動 bridge Caller↔1006 ✓
- 我們在 ChannelStateChange Up 事件設定 agent STT/MixMonitor
- **不需要 ami_bridge()**，Dial() 已自動處理

**不要修改 CTI 相關設定**（`/etc/asterisk/cti.conf`）。

---

## 各程式位置與職責

### amidaemon（`ai/ami/`，已模組化）

| 檔案 | 行數 | 職責 |
|------|------|------|
| `amidaemon_cyberon.py` | ~245 | 主程式：AMI 事件迴圈、Session 建立、轉接判斷 |
| `config.py` | ~55 | 所有可調參數（VAD 閾值、URL、Token、介入埠等） |
| `session.py` | ~30 | Session 物件 + 共用 sessions dict |
| `vad.py` | ~40 | Silero VAD 封裝（**換模型只改這支**） |
| `stt_reader.py` | ~200 | FIFO + VAD + Cyberon STT 斷句（**換 STT 引擎只改這支**） |
| `ami_actions.py` | ~80 | AMI 工具（send/redirect/setvar/mixmonitor/transfer） |
| `api_client.py` | ~140 | 呼叫 api_server + 推播 Machine C Conversation/Analysis |
| `machinec_client.py` | ~45 | 機器C HTTP 呼叫（Conversation、Analysis） |
| `intervention_server.py` | ~70 | 內部 HTTP 端點（:8201），接收 api_server forward 的介入指令 |
| `utils.py` | ~5 | `_ts()` 時間戳記 |

### apiserver（`/home/sipuser/apiserver/`，已模組化）

| 檔案 | 行數 | 職責 |
|------|------|------|
| `api_server.py` | ~195 | FastAPI 主程式 + 端點 |
| `config.py` | ~45 | 環境變數、URL、TTS Token、測試關鍵字 |
| `state.py` | ~10 | `sop_sessions` / `turn_counters` 共用 dict |
| `db.py` | ~110 | MySQL CRUD（cases / utterances / dialogue） |
| `sop_client.py` | ~95 | 110LLM HTTP 呼叫（new/push/observe/hangup/result） |
| `tts_client.py` | ~90 | Cyberon TTS gRPC + 8kHz 降採樣 |
| `payloads.py` | ~30 | FastAPI Pydantic models |

### 其他

| 程式 | 路徑 | 職責 |
|------|------|------|
| `cyberon_stt_client.py` | `ai/stt/cyberon_stt_client.py` | Cyberon STT WebSocket client |
| `sop_api_server.py` | 機器B `:8100` | 110LLM HTTP API wrapper |

---

## Dialplan 重要 context

| context | 觸發方式 | 功能 |
|---------|---------|------|
| `ai-call` | 撥 4000 | Answer + 設 MYUUID → ai-service |
| `ai-service` | ai-call | Wait(3) + UserEvent(AI_CALL_START) → caller-stt |
| `caller-stt` | ai-service / ai-speak(done) | UserEvent(STT_CALL_START) + Wait(300) |
| `ai-speak` | amidaemon AMI Redirect | GotoIf(AI_PLAY_TYPE=tts) → Playback → UserEvent(AI_PLAY_DONE) |
| `ai-end` | amidaemon / ai-speak | Hangup |
| `ai-transfer-dial` | amidaemon Redirect（轉接時） | `Dial(PJSIP/${TRANSFER_TARGET},60,Tt)` + Hangup |
| `ai-transfer-test` | 撥 4001 | 直接測試轉真人（TRANSFER_TEST_START UserEvent） |
| `agent-stt` | 備用（非 CTI 場景） | UserEvent(STT_AGENT_START) + Wait(300) |
| `cti-answer` | CTI 應答按鈕 | Answer() + Wait(3600)（**不可修改**） |

---

## AMI 關鍵動作（amidaemon → Asterisk）

TTS 播放：
```
Setvar: AI_NEXT=""
Setvar: AI_PLAY_TYPE="tts"
Setvar: AI_TTS_FILE="/home/sipuser/tts_audio/<uuid8>_<turn>"  (無副檔名)
Redirect: ai-speak, s, 1
```

轉接真人：
```
Setvar: TRANSFER_TARGET="1006"  (on caller channel)
Redirect: caller_channel → ai-transfer-dial, s, 1
```

---

## api_server 端點

| 端點 | 方向 | 呼叫時機 | 回傳 |
|------|------|---------|------|
| POST /call/start | amidaemon → | STT_CALL_START 後；transfer_mode=True 時僅建 cases 不啟 NLP | outputs, audio_path |
| POST /call/stt | amidaemon → | 每句完整 STT；transfer=True 時走 /observe 不播 TTS | outputs, audio_path, done, transfer, **case** |
| POST /call/end | amidaemon → | 掛斷 | status, **case** |
| POST /api/AiRobot/Transfer | 機器C → | 受理員按介入按鈕，或重新派工 | success |
| GET  /health | — | 健康檢查 | status, active_sessions |

transfer=True 時 `/call/stt` 內部行為：
- role="caller" → POST 機器B `/session/{id}/observe {"text":..., "role":"caller"}` → LLM 抽取更新欄位
- role="agent"  → POST 機器B `/session/{id}/observe {"text":..., "role":"agent"}` → 只 append transcript

`/call/stt` 和 `/call/end` 在 done=True 時帶回 `case` 欄位，供 amidaemon 推 Machine C Analysis(Initial/Final)。

---

## 機器B `/observe` endpoint（bridge 後使用）

```
POST /session/{session_id}/observe
{"text": "...", "role": "caller" | "agent"}
→ {"updated_fields": [...], "transcript_size": N}
```

限制：session 必須 done=true（轉接前 110LLM 已結束）才能使用。

---

## TTS 格式轉換

Cyberon TTS 輸出 16kHz WAV → api_server 用 `audioop.ratecv` 降採樣到 **8kHz 16-bit WAV** → 存 `/home/sipuser/tts_audio/<uuid8>_<turn>.wav` → Asterisk `format_wav.c` 才能播放。

TTS 語速：`TTS_SPEED=1.2`（env var，預設 1.2 倍速）

---

## STT 延遲分析

| 階段 | 時間 | 說明 |
|------|------|------|
| MixMonitor 內部 buffer | ~1s | Asterisk 固有延遲，無法繞過 |
| VAD 靜音閾值 | 0.8s | 短了會句中誤觸，長了延遲增加 |
| STT stop → FINAL | <0.3s | do_epd=True 串流模式，幾乎即時 |
| NLP + TTS 合成 | ~1s | 機器B網路RTT + LLM + gRPC |
| **合計（說完到聽到回覆）** | **~3-4s** | 目前架構的實際下限 |

若要繼續壓縮：需把 MixMonitor 換成 AudioSocket（零 buffer 直接接音訊），但改動較大。

---

## Silero VAD 客戶端靜音偵測（斷句邏輯）

已從 webrtcvad 改為 **Silero VAD**（神經網路模型，本地推論，無外部 API）。

```python
PCM_CHUNK_BYTES      = 800     # 50ms @ 8kHz，越小 STT 越早收到音訊
SILERO_CHUNK_SAMPLES = 256     # 32ms per inference chunk
SILERO_THRESHOLD     = 0.7     # 語音機率閾值（0–1，越高越不靈敏）
STT_VAD_SILENCE_MS   = 800     # 靜音達此值送 stop（調整歷程：800→200→500→800）
```

**靜音閾值調整歷程：**
- 800ms（原始）→ 使用者反應「回應太慢」
- 200ms（激進）→ 會切斷短停頓（如「五十（停頓）歲到六十歲」）
- 500ms → 仍會切斷自然換氣停頓（句子結束後正常呼吸即超過 500ms）
- 800ms（現行）→ 接受多等 0.8s 換取不切斷自然停頓

**斷句流程：**
1. `do_epd=True` 串流模式：server 邊辨識邊送 FINAL（自然停頓處拆段）→ 累積進 `final_buffer`
2. 客戶端 Silero VAD 偵測靜音 800ms → 送 `stop`
3. `client.stop(wait_final=True, timeout=5)` 等 server flush 最後一段
4. 合併 `pre_stop + post_stop` → 送 NLP（完整句子）
5. `silero.reset_states()` + 建新 `CyberonSTTClient` 等下一句

**執行緒安全：**
每個 `fifo_stt_reader` 用 `copy.deepcopy(_get_silero_vad())` 取得獨立模型副本，
避免 caller / agent 兩條 thread 共用 LSTM 狀態互相干擾。

**未來可考慮（尚未實作）：**
- 換用 AudioSocket 取代 MixMonitor（消除 ~1s buffer 延遲）
- Push-to-talk（使用者自控送出時機，需前端配合）

---

## MySQL（police110）

```
cases      — 一通電話一筆，存案件分類/地點/電話/case_json/時間戳
utterances — 逐句STT紀錄（role: caller/agent）
dialogue   — 一輪對話一筆（stt_text + nlp_reply + audio_path）
```

帳號：`api110` / `api110pass`
外部存取：`CREATE USER 'api110'@'%'`（MySQL 8.0 需先 CREATE USER 再 GRANT）

---

## 功能開關（amidaemon）

```python
ENABLE_DUAL_CHANNEL_STT = False  # True = 同時啟動 agent 1006 做雙通道 STT 測試（非 CTI 場景用）
TRANSFER_AGENT_EXT = "1006"      # 固定轉接受理員分機
```

---

## NLP 競態保護（nlp_pending）

```python
session.nlp_pending = True   # 送 NLP 前設定
session.nlp_pending = False  # HTTP 回應到位後（或失敗時）清除
```

**問題背景：**
VAD 靜音觸發後送 NLP，但 NLP 回覆需 ~1s。期間使用者若繼續說話，
VAD 會再次觸發並嘗試送出第二句，造成重複 NLP 呼叫、回覆錯位。

**解法：**
- `nlp_pending=True` 封鎖期間的新 STT 輸入（印 `⏭️ NLP處理中，略過`）
- `api_push_stt` 在 HTTP response 到位（不論成功/失敗）後清除旗標

---

## 機器C（前端）對接

機器C 在 `http://61.216.64.19:8055`，目前已對接的 API：

### 機器A → 機器C（已實作）

| 端點 | 觸發時機 | Payload |
|------|---------|---------|
| POST `/api/AiRobot/Conversation` | STT FINAL（Citizen）、NLP 回覆（AI）、agent STT（Officer）、開場白 | `{callId, speaker, DialogueText}` |
| POST `/api/AiRobot/Analysis` | 110LLM done=True 時 Initial；通話結束 Final | `{callId, caseTypeName, caseAddr, caseSummary, callerPhone, analysisState}` |

所有呼叫都在 daemon thread 內非同步送出，**失敗只 log 不影響主流程**。

### 機器C → 機器A（已實作 Transfer 介入）

| 端點 | 觸發時機 | Payload |
|------|---------|---------|
| POST `/api/AiRobot/Transfer` | 受理員按「介入」按鈕、原分機未接通需重新派工 | `{callId, agentExtension, reason, timestamp}` |

`reason`: `"intervene"`（介入）或 `"reassign"`（重新派工）。

api_server 收到後 forward 給 amidaemon 的 `:8201/transfer` 內部端點，amidaemon 立即：
1. 重置 `session.agent_channel` / `pending_agent_channel`
2. 設 `transfer_mode=True`、`pending_agent_ext=指定分機`
3. `ami_transfer_to_agent` → Redirect caller → ai-transfer-dial（**會自動打斷正在播的 TTS**）

### 待對接（待 Machine C 確認）

- POST `/api/AiRobot/CallStart`（機器A → 機器C）— 通話開始通知 + 容量檢查
- POST `/api/AiRobot/AssignAgent`（機器A → 機器C）— AI 自動轉接派工申請

詳細規格見 [docs/talk_toC/transfer-api-spec.md](docs/talk_toC/transfer-api-spec.md)。

---

## 介入端點（amidaemon 內部 HTTP，:8201）

amidaemon 啟動時於 `127.0.0.1:8201` 開一個小型 HTTP listener，由 api_server 內部 forward 介入指令過來。

```
POST http://127.0.0.1:8201/transfer
Body: {"callId": "<uuid>", "agentExtension": "<ext>", "reason": "intervene"|"reassign"}
Resp: {"success": true} | {"success": false, "error": "..."}
```

**為何分離：** 未來 api 機器與 ami 機器拆分時，把 `INTERVENTION_HOST` 從 `127.0.0.1` 改成內網 IP 即可，無需改其他程式碼。

**Reassign 處理：** Redirect 會自動打斷舊 Dial、Hangup 舊 PJSIP/分機通道。amidaemon 在 Redirect 前先重置 `agent_channel = None`，避免舊 Hangup 事件誤觸 session 清理。

---

## Log 格式

所有 print log 在字串末尾加上時間戳記（`HH:MM:SS`），由 `_ts()` 函式提供：

```python
def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")
```

NLP 回覆 log 同時顯示觸發句子：
```
🤖 [uuid8] 受理員（針對：你好，我要報案。）：請問您需要哪類協助？ 11:21:01
```

NLP 回傳空白時：
```
ℹ️ [uuid8] NLP 回空白（針對：你好，我要報案。）11:11:52
```

---

## 當前完成度

- [x] amidaemon ↔ Cyberon STT 串流（do_epd=True 串流模式）
- [x] webrtcvad 客戶端 VAD + 主動送 stop（取代 server EPD 控制）
- [x] FINAL 累積合併，避免斷句問題送給 NLP
- [x] amidaemon → api_server（call/start、call/stt、call/end）
- [x] api_server → MySQL（cases、utterances、dialogue）
- [x] api_server → 110LLM（新session、推文字、hangup、取result）
- [x] api_server → Cyberon TTS（gRPC, grpclib, skip-verify TLS）
- [x] TTS 16kHz→8kHz 降採樣（audioop.ratecv）
- [x] Asterisk Playback() 播放 TTS WAV
- [x] 開場白 TTS（/call/start 同步合成）
- [x] AI_PLAY_DONE 後回到 caller-stt 繼續監聽
- [x] STT_CALL_START 冪等保護（TTS 播完回來不重複初始化）
- [x] TTS 播放期間忽略 STT 輸入（STATE_SPEAKING guard）
- [x] 轉接真人（Redirect caller → ai-transfer-dial → Dial(PJSIP/1006)）
- [x] CTI 應答按鈕相容（Caller 為 Dial A-leg，CTI 正常管理 1006 B-leg）
- [x] Bridge 後雙通道 STT（caller+agent 各自 MixMonitor + STT）
- [x] Bridge 後 STT → 機器B /observe（caller 觸發 LLM 抽取，agent 只記 transcript）
- [x] 4001 直接測試轉真人入口
- [x] Silero VAD 取代 webrtcvad（本地神經網路模型，執行緒安全）
- [x] nlp_pending 競態保護（避免重複送 NLP）
- [x] 全 log 加時間戳記（_ts()）
- [x] NLP 回覆 log 顯示觸發句子（針對：...）
- [x] NLP 回空白時印 ℹ️ log（可見化 110LLM 未回應）
- [x] amidaemon 模組化（config / session / vad / stt_reader / ami_actions / api_client / utils）
- [x] api_server 模組化（config / state / db / sop_client / tts_client / payloads）
- [x] 機器C 對接：Conversation 推送（Citizen/AI/Officer）
- [x] 機器C 對接：Analysis 推送（Initial/Final）
- [x] 機器C 對接：介入端點 `POST /api/AiRobot/Transfer`（api_server 收 → amidaemon :8201）
- [ ] 機器C 對接：`/CallStart`（含容量檢查）— 規格已給機器C，待對方實作
- [ ] 機器C 對接：`/AssignAgent`（自動轉接派工）— 規格已給機器C，待對方實作
- [ ] 滿線機制（AssignAgent 回 null → 播忙線提示 → 掛斷）
- [ ] 從 4000 AI 完整流程 → 轉接 → Bridge 後 NLP 端對端測試
- [ ] 轉接分機改用機器C 給的動態號碼（目前 NLP 觸發仍走預設 1006）
- [ ] VAD 斷句最佳化（目前 800ms；可考慮 AudioSocket 或 Push-to-talk）
