# 110 接警系統整合計畫

## 系統拓撲

```
┌──────────────────────────────────────┐        ┌──────────────────────────────┐
│          機器 A（Asterisk）            │        │   機器 B（NLP + STT/TTS）     │
│                                      │        │                              │
│  amidaemon_cyberon.py                │        │  Cyberon STT Server          │
│      │  PCM 串流 ───────────────────────────►  │  Cyberon TTS Server（待確認）│
│      │  ◄─────────────── FINAL 文字  │        │  110LLM sop_api_server :8100 │
│      │                              │        └──────────────────────────────┘
│      ▼                              │
│  api_server.py :8200                │
│      │                              │
│      ├─► MySQL                      │
│      ├─► 110LLM (機器B:8100) ───────────────►
│      └─► Cyberon TTS（待實作）───────────────►
│                                      │
└──────────────────────────────────────┘
```

- Asterisk + api_server + MySQL 同一台（機器 A）
- NLP + STT + TTS 同一台（機器 B，192.168.5.204）
- 所有訊息以 **UUID** 作為唯一識別值

---

## 設計決策

| 問題 | 決定 | 原因 |
|------|------|------|
| STT 結果在哪裡產出？ | 機器B 運算，機器A 的 amidaemon 收到後送 api_server | amidaemon 已有 on_result callback，api_server 在同機，不用繞路 |
| TTS 用音檔還是即時串流？ | WAV 音檔 | Asterisk 原生支援 Playback()，最簡單可靠；穩定後再考慮串流 |
| api_server 誰來做？ | 暫時自己頂上 | 已建好 `ai/api/api_server.py`，等原負責人接手後可直接替換 |

---

## 完整目標流程

```
1. 報警人說話
      ↓
2. amidaemon 串流 PCM → Cyberon STT（機器B）
      ↓  on_result 收到 FINAL
3. amidaemon POST /call/stt → api_server（同機，port 8200）
   { uuid, text, role:"caller" }
      ↓
4. api_server → MySQL 寫 utterances 資料表
      ↓
5. api_server POST /session/{id}/input → 110LLM（機器B:8100）
   { text }
      ↓
6. 110LLM 回傳受理員回覆文字
      ↓
7. api_server → MySQL 更新 nlp_reply
      ↓
8. api_server → Cyberon TTS（機器B，TODO）→ WAV 音檔
      ↓
9. api_server 回傳 { audio_path } 給 amidaemon
      ↓
10. amidaemon 呼叫 Asterisk AMI 播放 WAV
      ↓ 報警人聽到回覆，再次說話 → 回到步驟 2
```

---

## 分步執行計畫

### Step 1：建立 MySQL 資料庫

在機器 A 執行：

```bash
# 確認 MySQL 有在跑
systemctl status mysql

# 登入 MySQL
mysql -u root -p

# 建立資料庫與資料表（貼入以下 SQL）
```

```sql
CREATE DATABASE IF NOT EXISTS police110 CHARACTER SET utf8mb4;
USE police110;

-- 案件主表（一通電話一筆）
CREATE TABLE IF NOT EXISTS cases (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    uuid        VARCHAR(64) NOT NULL UNIQUE,
    channel     VARCHAR(128),
    nlp_reply   TEXT,
    act_class   VARCHAR(64),
    location    VARCHAR(256),
    caller_phone VARCHAR(32),
    case_json   JSON,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    ended_at    DATETIME,
    INDEX idx_uuid (uuid)
);

-- 逐句紀錄表（caller/agent 每句話一筆）
CREATE TABLE IF NOT EXISTS utterances (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    uuid        VARCHAR(64) NOT NULL,
    role        VARCHAR(16),
    stt_text    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_uuid (uuid)
);
```

確認建立成功：
```sql
SHOW TABLES;
-- 應顯示 cases, utterances
EXIT;
```

---

### Step 2：啟動機器 B 的 110LLM API

在機器 B 執行：

```bash
# 安裝（只需一次）
pip install fastapi uvicorn

# 先確認 SopEngine 可以跑
cd <110LLM_0426 的路徑>
printf "板橋發生車禍，有人受傷\n" | python3 sop_110_assistant.py \
    --input-mode text \
    --model-dir ../TW-110-Bert-v4/checkpoint-83352 \
    --flows-dir ./flows \
    --llm none

# 把 sop_api_server.py 放進 110LLM_0426 目錄，然後啟動
uvicorn sop_api_server:app --host 0.0.0.0 --port 8100
```

從機器 A 測試連線：
```bash
curl http://192.168.5.204:8100/health
# 預期：{"status":"ok","sessions":0}
```

---

### Step 3：啟動機器 A 的 api_server

```bash
# 安裝（只需一次）
pip install fastapi uvicorn pymysql

# 啟動
cd /home/sipuser/apiserver
uvicorn api_server:app --host 0.0.0.0 --port 8200
```

手動測試整條流程（模擬一通電話）：

```bash
# 1. 通話開始
curl -s -X POST http://localhost:8200/call/start \
  -H "Content-Type: application/json" \
  -d '{"uuid":"test-001","channel":"SIP/1234"}' | python3 -m json.tool

# 2. 送 STT 結果（把 uuid 換成剛才的）
curl -s -X POST http://localhost:8200/call/stt \
  -H "Content-Type: application/json" \
  -d '{"uuid":"test-001","text":"板橋發生車禍，有人受傷","role":"caller"}' \
  | python3 -m json.tool

# 3. 看 MySQL 有沒有資料
mysql -u root -p police110 -e "SELECT * FROM utterances;"

# 4. 通話結束
curl -s -X POST http://localhost:8200/call/end \
  -H "Content-Type: application/json" \
  -d '{"uuid":"test-001"}' | python3 -m json.tool
```

---

### Step 4：接上 amidaemon_cyberon.py

Step 3 測試通過後，把 amidaemon 的 SOP 直連改為透過 api_server。

**修改 `amidaemon_cyberon.py` 的設定區：**

```python
# 把原本的 SOP_SERVER_URL 改成指向本機 api_server
API_SERVER_URL = "http://127.0.0.1:8200"
```

amidaemon 的呼叫邏輯：
- `STT_CALL_START` 時 → `POST /call/start`
- `on_result` FINAL → `POST /call/stt`，收到 `audio_path` 後播音
- 掛斷時 → `POST /call/end`

（這步等 Step 3 測試通過後再做，我會直接改程式碼）

---

### Step 5：Cyberon TTS（待實作）

等確認 Cyberon TTS API 端點後，補全 `api_server.py` 裡的 `_tts_synthesize()` 函式。

目前位置：`/home/sipuser/apiserver/api_server.py` 的 `_tts_synthesize` 函式，有 `TODO` 標記。

---

## 目前阻塞點

| 項目 | 狀態 | 需要做什麼 |
|------|------|-----------|
| MySQL 建立 | ⬜ 未做 | 執行 Step 1 的 SQL |
| 機器B 110LLM API | ⬜ 未做 | 執行 Step 2 |
| 機器A api_server | ⬜ 未做 | 執行 Step 3 |
| Cyberon TTS API | ❓ 未知 | 詢問 Cyberon 廠商或查機器B |

---

## 檔案清單

| 檔案 | 位置 | 說明 |
|------|------|------|
| api_server.py | `/home/sipuser/apiserver/api_server.py` | 機器A 的中控 API（與 Asterisk 專案分開） |
| sop_api_server.py | `ai/llm/110LLM_0426/sop_api_server.py` | 機器B 的 110LLM API（部署到機器B）|
| amidaemon_cyberon.py | `ai/ami/amidaemon_cyberon.py` | Asterisk AMI Daemon |
