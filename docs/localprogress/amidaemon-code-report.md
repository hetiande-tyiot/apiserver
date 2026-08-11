# amidaemon_cyberon 程式碼報告

_最後更新：2026-05-13_

---

## 一、系統定位

`ai/ami/` 是機器 A 的核心 AI 控制層，負責：

- 監聽 Asterisk AMI 事件（通話開始、TTS 播完、掛斷等）
- 從 FIFO 讀取 PCM 音訊，用 VAD 判斷斷句，送給 Cyberon STT
- 將 STT 文字推給 api_server（本機 :8200），取得 NLP 回覆與 TTS 路徑
- 控制 Asterisk 播放 TTS、轉接受理員

---

## 二、模組一覽

```
ai/ami/
├── config.py           (47行)  所有可調參數
├── utils.py             (5行)  _ts() 時間戳記
├── session.py          (30行)  Session 物件 + 共用狀態
├── vad.py              (40行)  Silero VAD 封裝
├── ami_actions.py      (77行)  AMI 動作函式
├── api_client.py      (104行)  api_server HTTP 呼叫
├── stt_reader.py      (197行)  FIFO + VAD + STT 斷句
└── amidaemon_cyberon.py(243行) 主程式：事件迴圈
```

---

## 三、各模組說明

### `config.py` — 參數集中區
所有需要手動調整的數值都在這裡，分四區：

| 區塊 | 主要參數 |
|---|---|
| AMI 連線 | `AMI_HOST`, `AMI_PORT`, `AMI_USER`, `AMI_PASS` |
| api_server | `API_SERVER_URL`, `API_TIMEOUT` |
| Cyberon STT | `CYBERON_URL`, `CYBERON_TOKEN`, `PCM_CHUNK_BYTES` |
| Silero VAD | `SILERO_THRESHOLD`（靈敏度）, `STT_VAD_SILENCE_MS`（斷句閾值） |
| 功能開關 | `ENABLE_DUAL_CHANNEL_STT`, `TRANSFER_AGENT_EXT` |

---

### `session.py` — 通話狀態
一通電話對應一個 `Session` 物件，儲存於 `sessions` dict（以 UUID 為 key）。

```
Session 欄位：
  caller_channel      Asterisk channel 名稱（如 PJSIP/1003-xxx）
  agent_channel       受理員接通後的 channel
  state               INIT / LISTENING / SPEAKING / ENDED
  transfer_mode       True = 已轉接，STT 走 /observe 不走 NLP
  nlp_pending         True = NLP 處理中，封鎖下一句 STT 輸入
  stt_initialized     防止 STT_CALL_START 重複初始化
```

`sessions` dict 被所有模組共用（Python dict 物件參照共享）。

---

### `vad.py` — VAD 模型
封裝 Silero VAD（神經網路，本機推論，不需外部 API）。

- `get_model()` — 全域單例，程式啟動時預載一次
- `new_instance()` — 每個 `fifo_stt_reader` thread 呼叫一次，取得 `deepcopy`

> **為何要 deepcopy？**  
> Silero 內部有 LSTM 狀態（`reset_states()`）。若 caller 和 agent 兩條 thread 共用同一個模型，LSTM 狀態會互相蓋掉，導致 VAD 判斷錯誤。

---

### `ami_actions.py` — AMI 工具
對 Asterisk 下指令的底層函式，全部透過 TCP socket 送出 AMI 文字協定。

| 函式 | 用途 |
|---|---|
| `ami_send` | 最底層：送一行 AMI 訊息 |
| `ami_login` | 登入 AMI |
| `ami_redirect` | 將 channel 跳到指定 context |
| `ami_setvar` | 設定 channel 變數（如 AI_TTS_FILE） |
| `ami_start_mixmonitor` | 開始錄音到 FIFO（供 STT 讀取） |
| `ami_transfer_to_agent` | 轉接：Redirect caller → ai-transfer-dial → Dial(PJSIP/1006) |
| `ami_bridge` | 手動 bridge（僅 ENABLE_DUAL_CHANNEL_STT 使用） |
| `ami_originate_agent` | 直接 originate（僅非 CTI 測試用） |

> **CTI 相容性重點：** 轉接必須用 `ami_transfer_to_agent`（Redirect → Dial），  
> 不可直接 originate，否則 CTI 的應答按鈕無法正常 bridge。

---

### `api_client.py` — api_server 呼叫

| 函式 | 端點 | 時機 |
|---|---|---|
| `api_call_start` | POST `/call/start` | STT 初始化後，建案件 + 取開場白 TTS |
| `api_push_stt` | POST `/call/stt` | 每句 STT FINAL 後送 NLP，回傳 TTS 路徑 |
| `api_call_end` | POST `/call/end` | 掛斷後通知收尾 |

`api_push_stt` 內部邏輯：
1. HTTP POST → 等 NLP 回覆
2. 清除 `session.nlp_pending`（不論成功失敗）
3. 若有 `audio_path` → 觸發 TTS 播放（AMI Redirect → ai-speak）
4. 若回傳 `transfer=true` → 設定轉接（有 TTS 則等播完再轉，無則立即）

---

### `stt_reader.py` — 斷句核心

**主函式 `fifo_stt_reader`** 斷句流程：

```
PCM 從 FIFO 流入
  → 50ms 一塊送給 CyberonSTTClient（即時串流辨識）
  → 同時推進 Silero VAD（32ms 子塊推理）
      有語音 → speech_started=True，silence_frames 重置
      靜音 → silence_frames++，達 SILENCE_TRIGGER 觸發：
          1. 收集 stop 前累積的 FINAL（pre_stop）
          2. client.stop(wait_final=True) 等 server flush 最後片段
          3. 收集 stop 後的 FINAL（post_stop）
          4. 合併 → 送 NLP（若 nlp_pending=True 則略過）
          5. reset_states() + 建新 client → 繼續監聽
```

**`setup_agent_stt`** — 受理員接通後呼叫，為 agent channel 建立獨立的 FIFO + STT reader。

---

### `amidaemon_cyberon.py` — 主程式

只做兩件事：

1. **`main()`** — 預載 VAD → 連線 AMI → 進入事件迴圈
2. **`ami_event_loop()`** — 監聽 AMI 事件，分派到各模組

主要處理的事件：

| AMI 事件 | 動作 |
|---|---|
| `UserEvent(AI_CALL_START)` | 建立 Session |
| `UserEvent(STT_CALL_START)` | 建 FIFO + 啟 STT reader + 呼叫 call_start |
| `UserEvent(AI_PLAY_DONE)` | TTS 播完 → 回 caller-stt 或執行轉接 |
| `UserEvent(TRANSFER_TEST_START)` | 撥 4001 直接測試轉接流程 |
| `Newchannel` | 追蹤 agent channel 名稱（響鈴中） |
| `ChannelStateChange Up` | Agent 接通 → 啟動 agent STT |
| `Hangup` | 清理 Session，通知 api_call_end |

---

## 四、資料流總覽

```
來電
 │
 ▼
Asterisk (dialplan)
 │  UserEvent(STT_CALL_START)
 ▼
amidaemon_cyberon.py
 │  建 FIFO，啟 MixMonitor
 ▼
stt_reader.py (fifo_stt_reader)
 │  PCM → Silero VAD → CyberonSTTClient
 │  FINAL 累積 → VAD 靜音觸發
 ▼
api_client.py (api_push_stt)
 │  POST /call/stt → api_server:8200
 │       └─ 110LLM (機器B:8100)
 │       └─ Cyberon TTS (機器B:8088)
 │  回傳 audio_path + NLP 文字
 ▼
ami_actions.py
 │  AMI Setvar(AI_TTS_FILE) + Redirect(ai-speak)
 ▼
Asterisk → Playback(WAV) → 報警人聽到回覆
```

---

## 五、可替換點

| 要替換 | 修改哪裡 |
|---|---|
| VAD 模型（Silero → 其他） | `vad.py` |
| STT 引擎（Cyberon → 其他） | `stt_reader.py` 的 `CyberonSTTClient` 載入與 `make_client()` |
| 各項數值參數 | `config.py` |
| api_server 端點格式 | `api_client.py` |
