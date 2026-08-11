# apiserver 專案筆記

_最後更新：2026-05-17_

> **交接說明：** 本筆記為 apiserver 元件的獨立說明，未來此服務若交由他人負責，
> 此檔案應為主要交接參考。涵蓋啟動方式、API 規格、外部依賴、設定值與已知問題。

---

## 概述

apiserver 是機器A 的**中控 HTTP 服務（FastAPI）**，扮演 amidaemon 與外部各系統的單一窗口：

- 對 amidaemon：提供 `/call/start`、`/call/stt`、`/call/end` 三個核心端點
- 對機器B 110LLM：呼叫 `/session/*` 各端點
- 對機器B Cyberon TTS：透過 gRPC 合成語音，降採樣後存 WAV
- 對 MySQL（police110）：寫入 cases / utterances / dialogue
- 對機器C 前端：接收 `/api/AiRobot/Transfer`，forward 給 amidaemon

**核心職責**：把外部多個服務（LLM、TTS、DB）的呼叫包起來，
讓 amidaemon 只需要打 3 個 HTTP 端點就能完成一通電話的所有 AI 處理。

---

## 啟動 / 停止

```bash
# 啟動
cd /home/sipuser/apiserver
source apienv/bin/activate
uvicorn api_server:app --host 0.0.0.0 --port 8200

# 停止：Ctrl-C 或 pkill
pkill -f uvicorn
```

健康檢查：

```bash
curl http://127.0.0.1:8200/health
# {"status":"ok","active_sessions":0}
```

依賴套件：`fastapi`, `uvicorn`, `pydantic`, `requests`, `pymysql`, `grpclib`, `protobuf`, `audioop`（標準函式庫）。

---

## 模組結構

`/home/sipuser/apiserver/`

| 檔案 | 行數 | 職責 |
|------|------|------|
| `api_server.py` | ~195 | FastAPI 主程式 + 5 個端點 |
| `config.py` | ~45 | 所有環境變數與參數 |
| `state.py` | ~10 | 程序內共用狀態（uuid → session_id / turn） |
| `db.py` | ~110 | MySQL CRUD 封裝 |
| `sop_client.py` | ~95 | 機器B 110LLM HTTP 呼叫 |
| `tts_client.py` | ~90 | Cyberon TTS gRPC + 8kHz 降採樣 |
| `payloads.py` | ~30 | FastAPI Pydantic 請求 model |
| `service_pb2.py` / `service_grpc.py` | — | TTS gRPC stub（不要動，由 .proto 編譯產生） |
| `pyrightconfig.json` | — | IDE 路徑解析設定 |

---

## API 端點

### A. 對內（amidaemon → apiserver）

#### `POST /call/start`
通話開始，建 cases 紀錄、開 110LLM session、合成開場白 TTS。

Request:
```json
{ "uuid": "...", "channel": "PJSIP/...", "transfer_mode": false }
```

Response:
```json
{ "uuid": "...", "outputs": ["開場白..."], "audio_path": "/home/sipuser/tts_audio/xxx.wav", "status": "started" }
```

- `transfer_mode=true` 時僅建 cases，不開 NLP session（用於 4001 測試入口）。

#### `POST /call/stt`
每句 STT FINAL 後呼叫，是核心端點。

Request:
```json
{ "uuid": "...", "text": "我要報案", "role": "caller", "transfer": false }
```

行為分支：
1. **測試關鍵字** `"測試轉接"` → 直接回 `transfer=true`（**正式上線需移除**）
2. **`transfer=true`**（bridge 模式）→ 走機器B `/observe`，回 outputs=[] 不播 TTS
3. **正常 caller** → 走機器B `/input` → 取 NLP outputs → 合成 TTS → 寫 dialogue

Response:
```json
{
  "uuid": "...",
  "outputs": ["NLP 回覆..."],
  "audio_path": "/home/.../xxx.wav",
  "done": false,
  "error": null,
  "transfer": false,
  "case": null
}
```

- `done=true` 時 `case` 帶回完整案件 JSON（供 amidaemon 推機器C Analysis(Initial)）。

#### `POST /call/end`
通話結束，呼叫 110LLM hangup、取最終 case、更新 cases.ended_at。

Response 帶 `case` 欄位供 amidaemon 推機器C Analysis(Final)。

#### `GET /health`
健康檢查，回傳 `active_sessions` 數量。

### B. 對外（機器C → apiserver）

#### `POST /api/AiRobot/Transfer`
受理員按介入按鈕，或重新派工。內部 forward 給 amidaemon `:8201/transfer`。

Request:
```json
{ "callId": "...", "agentExtension": "1008", "reason": "intervene" | "reassign" }
```

apiserver 收到後直接轉發，amidaemon 處理實際的 AMI 操作。

---

## 設定參數（`config.py`）

所有參數都可透過環境變數覆寫。

| 參數 | 預設 | 說明 |
|------|------|------|
| `SOP_SERVER_URL` | `http://192.168.5.204:8100` | 機器B 110LLM |
| `AMIDAEMON_URL` | `http://127.0.0.1:8201` | amidaemon 介入端點 |
| `MYSQL_HOST` | `127.0.0.1` | MySQL 主機 |
| `MYSQL_USER` | `api110` | MySQL 帳號 |
| `MYSQL_PASS` | `api110pass` | MySQL 密碼 |
| `MYSQL_DB` | `police110` | 資料庫名稱 |
| `TTS_HOST` | `192.168.5.204` | 機器B Cyberon TTS |
| `TTS_PORT` | `8088` | TTS gRPC port |
| `TTS_SPEED` | `1.2` | TTS 語速倍率（1.0 = 正常） |
| `TTS_TOKEN` | （內含） | TTS 授權 token |
| `TTS_AUDIO_DIR` | `/home/sipuser/tts_audio` | TTS WAV 存放路徑 |
| `HTTP_TIMEOUT` | `10` | 對外 HTTP 呼叫 timeout（秒） |
| `TEST_TRANSFER_KEYWORD` | `"測試轉接"` | 測試用觸發詞（**正式上線需移除**） |

---

## 外部依賴

### 機器B 110LLM（`sop_client.py`）

| 端點 | 用途 |
|------|------|
| `POST /session/new` | 建新 session，回 session_id + 開場白 outputs |
| `POST /session/{id}/input` | 推 caller 文字，回 outputs + done + error |
| `POST /session/{id}/observe` | Bridge 後雙通道送進來（不產生 AI 回覆） |
| `POST /session/{id}/hangup` | session 收尾 |
| `GET /session/{id}/result` | 取最終案件 JSON |

### 機器B Cyberon TTS（`tts_client.py`）

- gRPC over TLS（跳過憑證驗證），輸入文字 → 輸出 16kHz WAV bytes
- apiserver 用 `audioop.ratecv` 降採樣到 **8kHz 16-bit WAV**（Asterisk `format_wav.c` 要求）
- 存到 `TTS_AUDIO_DIR/<uuid8>_<turn>.wav`，回傳路徑給 amidaemon

### MySQL（`db.py`）

```
cases      uuid (PK), channel, act_class, location, caller_phone, nlp_reply,
           case_json, created_at, updated_at, ended_at
utterances id (PK), uuid, role, stt_text, created_at
dialogue   id (PK), uuid, turn, stt_text, nlp_reply, audio_path, created_at
```

操作函式都在 `db.py`：`insert_case`, `insert_utterance`, `insert_dialogue`,
`update_nlp`, `save_case`, `update_case_ended`。

每次呼叫都新建連線（`autocommit=True`），未做連線池——目前負載低足夠。

### amidaemon（介入指令 forward）

```
POST http://127.0.0.1:8201/transfer
Body: { "callId", "agentExtension", "reason" }
```

apiserver 只是 thin proxy，所有實際的 AMI 操作在 amidaemon 內處理。

---

## 共用狀態（`state.py`）

```python
sop_sessions:  dict[str, str]   # uuid → 110LLM session_id
turn_counters: dict[str, int]   # uuid → 目前 TTS 輪次（檔名用）
```

**注意**：這是程序內記憶體狀態，**重啟 apiserver 會清空**。
進行中的通話若 apiserver 重啟，後續 `/call/stt` 會找不到 sop session，導致 NLP 失效。

---

## 重要設計決策

### 1. 為何 apiserver 統一收所有外部呼叫？

amidaemon 跑事件迴圈 + STT thread，混入大量業務邏輯會難維護。
apiserver 作為 HTTP 中控層讓 amidaemon 只關心 AMI 與 FIFO。

未來 api / ami 機器拆分時，調整 `AMIDAEMON_URL` 與 `API_SERVER_URL` 即可。

### 2. TTS 同步 vs 非同步

`tts_client.synthesize()` 是**同步**呼叫，因為 `/call/stt` 必須等 TTS 完成才能回傳 `audio_path`，
不然 amidaemon 拿不到檔案播放。內部用 `ThreadPoolExecutor` 跑 asyncio gRPC，
從同步端點呼叫不會 block FastAPI 的事件迴圈。

### 3. `case` 欄位在 `/call/stt` 與 `/call/end` 都帶回

讓 amidaemon 在「AI 對話結束（done=true）」與「通話掛斷（call/end）」兩個時間點，
都能拿到案件資料推給機器C Analysis(Initial / Final)。
不在 apiserver 直接呼叫機器C，是為了維持單向依賴：機器C 對接邏輯都在 amidaemon `machinec_client.py`。

### 4. `transfer_mode` 與 4001 測試入口

撥 4001 走 `ai-transfer-test` context，amidaemon 帶 `transfer_mode=true` 呼叫 `/call/start`。
此模式下 apiserver 不開 110LLM session，純粹建立 cases 紀錄供後續 bridge 雙通道紀錄使用。

---

## Log 範例

```
🤖 [4975a464] 開場白：新北市警局110您好...
🔊 [4975a464] TTS 完成：54000 bytes，1.23s → /home/sipuser/tts_audio/4975a464_1.wav
📝 [4975a464] [caller] 我要報案
🤖 [4975a464] 受理員：請問需要哪類協助？
✅ [4975a464] 案件 JSON 存入 MySQL（分類：找人）
ℹ️ [4975a464] 業務性結束：轉接專人
📥 [Transfer/intervene] 4975a464 → PJSIP/1006
```

警告：
- `⚠️  110LLM new session 失敗：...`
- `⚠️  TTS 失敗 [xxx]：...`
- `⚠️  MySQL ... 失敗：...`
- `⚠️  forward to amidaemon 失敗：...`

---

## 已知問題 / TODO

- [ ] `TEST_TRANSFER_KEYWORD` 正式上線需移除
- [ ] 未做連線池：高負載下 MySQL 連線開關可能成為瓶頸
- [ ] 進行中通話若 apiserver 重啟，`sop_sessions` 遺失導致 NLP 中斷（需考慮 Redis 或檔案持久化）
- [ ] 無 retry 機制：任一外部呼叫失敗就回錯，沒做指數退避重試
- [ ] 待補：`POST /api/AiRobot/CallStart` 與 `/AssignAgent`（規格已給機器C，待對方實作後本側才補對應的呼叫）

---

## 相關文件

- 整體架構：`docs/localprogress/project-notes.md`
- amidaemon 筆記：`docs/localprogress/amidaemon-notes.md`
- 機器C 對接 API 規格：`docs/talk_toC/transfer-api-spec.md`
- 與機器B 的歷史討論：`docs/talk_toB/` 與 `docs/talk_fromB/`

---

## 換環境 / 部署快速說明

目前所有狀態：
- 程式碼：`/home/sipuser/apiserver/`
- venv：`/home/sipuser/apiserver/apienv/`
- TTS 音檔：`/home/sipuser/tts_audio/`（Asterisk 與 apiserver 共用）
- MySQL：本機 `police110` database，帳號 `api110`

換機部署需要：
1. 複製 `/home/sipuser/apiserver/` 整個目錄（含 service_pb2/grpc）
2. 重建 venv：`python3 -m venv apienv && source apienv/bin/activate && pip install -r requirements.txt`
3. 設定環境變數（或改 `config.py` 預設值）
4. MySQL：建立 `police110` database 與表結構，設定 `api110` 帳號
5. **TTS 音檔目錄**：若與 Asterisk 不同機，需 NFS 或類似機制共享
6. 啟動：`uvicorn api_server:app --host 0.0.0.0 --port 8200`

> 目前尚未產出 `requirements.txt`，需要時可用 `pip freeze` 從現有 venv 產生。
