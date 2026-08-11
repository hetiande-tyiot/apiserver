# 機器 B 部署指引

## 你的角色
你是機器 B（NLP + STT/TTS 機器）的負責人。
機器 A（Asterisk，192.168.5.x）的 api_server 會透過 HTTP 呼叫你這邊的服務。

## 你需要做的事：啟動 110LLM API Server

### 前提
這台機器上應該已經有 110LLM 的程式碼，目錄結構如下：
```
110LLM_0426/
    sop_110_assistant.py
    sop_api_server.py       ← 剛放進來的新檔案
    flows/
    ...
TW-110-Bert-v4/
    checkpoint-83352/
```

### Step 1：確認 SopEngine 可以正常運行

```bash
cd <110LLM_0426 的路徑>

printf "板橋發生車禍，有人受傷\n" | python3 sop_110_assistant.py \
    --input-mode text \
    --model-dir ../TW-110-Bert-v4/checkpoint-83352 \
    --flows-dir ./flows \
    --llm none
```

看到案件分類結果就表示正常。

### Step 2：安裝套件

```bash
pip install fastapi uvicorn
```

如果出現 externally-managed-environment 錯誤：
```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn
```

### Step 3：啟動 API Server

```bash
cd <110LLM_0426 的路徑>
uvicorn sop_api_server:app --host 0.0.0.0 --port 8100
```

看到以下訊息表示成功：
```
✅ 分類器就緒
INFO:     Uvicorn running on http://0.0.0.0:8100
```

### Step 4：確認可以從外部連線

在機器 B 本機測試：
```bash
curl http://localhost:8100/health
```

預期回應：
```json
{"status": "ok", "sessions": 0}
```

---

## API 端點說明（給機器 A 參考）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/session/new` | 建立新通話 session，回傳 session_id 與開場白 |
| POST | `/session/{id}/input` | 推入一句 STT 文字，回傳受理員回覆 |
| POST | `/session/{id}/hangup` | 通話結束 |
| GET  | `/session/{id}/result` | 取得完整案件 JSON |
| GET  | `/health` | 健康檢查 |

---

## 如果有問題
把錯誤訊息回傳給機器 A 那邊的開發者。
