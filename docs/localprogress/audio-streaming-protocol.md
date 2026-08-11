# 賽微 Cyberon STT WebSocket 串接筆記

> **目的**：把 Asterisk 通話音訊（caller / agent）即時串到賽微 Cyberon STT Server，取代目前「切 WAV → HTTP POST」的舊流程。
>
> **協定來源**：賽微提供的官方文件（v1.0.5）。本筆記只整理我們系統會用到的部份與對接方式，協定權威以官方 PDF 為準。
>
> 官方文件：`台研科技/202603101/STT_webSocket/Document/Cyberon_STT_Protocol_Document.pdf`
> 官方範例（Go）：`台研科技/202603101/STT_webSocket/SampleCode/Golang/main.go`

---

## 1. 連線資訊

| 項目 | 值 |
|------|------|
| URL | `ws://192.168.5.204:8890/SttProxy/recognition` |
| Token | 在 `API_Info.txt`，由賽微提供 |
| Domain | `freeSTT-zh-TW` |
| Sample rate | **8000 Hz（與 Asterisk telephony 原生相同，無須 resample）** |
| 音訊編碼 | `audio/L16; rate=8000`（Linear PCM 16-bit Little-endian, mono） |

### ⚠️ Port 踩坑紀錄（2026-05-02 測試）

- `ws://192.168.5.204/SttProxy/recognition`（port 80）→ HTTP **301** redirect 到 `https://`，WebSocket 無法用
- `wss://192.168.5.204/SttProxy/recognition`（port 443）→ SSL 握手成功，但回 **404**（nginx 未設 WebSocket proxy）
- **`ws://192.168.5.204:8890/SttProxy/recognition` → 正確！回 `101 Switching Protocols`，Server 第一則訊息 `{"state":"listening"}`**

Port 443 是 Cyberon Web UI（`/CyberonLocalTools/login`）的 nginx，不是 STT WebSocket Server。  
STT WebSocket Server 直接跑在 **port 8890**，不走 SSL。

---

## 2. 訊息流程

```
Client (我們的 daemon)                              Cyberon STT Server
     |                                                       |
     |  ── WebSocket handshake (HTTP 101) ──>                |
     |                                                       |
     |  <── text: {"state":"listening"} ── (#1)              |
     |                                                       |
     |  ── text: {"action":"start","domain":...,"token":...} |
     |          "type":"audio/L16; rate=8000",               |
     |          "isGetPartial":true, "bIsDoEPD":true,        |
     |          "bIsContinueRecog":true ──>                  |
     |                                                       |
     |  ── binary: PCM chunk ──>                             |
     |  ── binary: PCM chunk ──>                             |
     |                                       <── text: {"state":"result","isFinish":false,
     |                                                    "recog_result":"我想要"}   (partial)
     |  ── binary: PCM chunk ──>                             |
     |                                       <── text: {"state":"result","isFinish":true,
     |                                                    "recog_result":"我想要查詢餘額",
     |                                                    "sen_start_ms":1200,"sen_end_ms":3800}
     |                                                                              (utterance final)
     |  ── binary: PCM chunk ──>  (持續，bIsContinueRecog=true 不會中斷)             |
     |  ...                                                                         |
     |                                       <── text: {...isFinish:true...}  (下一個 utterance)
     |  ...                                                                         |
     |  ── text: {"action":"stop"} ──>                                              |
     |                                       <── text: {...isFinish:true...}  (flush 殘餘)
     |                                       <── text: {"state":"listening"} ── (#2)
     |  ── close ──>                                                                |
     |                                                       <── close ──          |
```

**關鍵點**：
- `listening` 訊息出現 **兩次**：連線後（#1）→ 送 start 開始串流；stop 之後（#2）→ 表示 server 已 flush，可以關連線。
- `bIsContinueRecog=true` 之下，**一通電話一條 WebSocket 連線**，中間 server 自己用 EPD 切句、分多次送 `isFinish:true` final，不需要重新 start。

---

## 3. start 訊息欄位（client → server）

| 欄位 | 必要 | 我們用的值 | 說明 |
|------|------|-----------|------|
| `action` | ✅ | `"start"` | 固定 |
| `domain` | ✅ | `"freeSTT-zh-TW"` | 辨識模組 |
| `platform` | ✅ | `"asterisk"` | 隨意填，標識來源 |
| `uid` | ✅ | `"<uuid>-<role>"` | 通話 UUID + role，方便 server log 比對 |
| `token` | ✅ | （見 API_Info） | 賽微提供 |
| `type` | ✅ | `"audio/L16; rate=8000"` | 8kHz mono s16le |
| `isGetPartial` | ❌ | `true` | **打開**，邊講邊出字幕 |
| `bIsDoEPD` | ❌ | `true`（預設） | server 端做端點偵測，**我們本地不再做 VAD** |
| `bIsContinueRecog` | ❌ | `true`（預設） | 連續辨識，一通電話一條連線 |
| `epdFrameNum` | ❌ | `80`（預設）| EPD 長度 = 0.8 秒（100 = 1 秒） |
| `nBestNum` | ❌ | `1`（預設）| 只要最佳結果 |

---

## 4. 辨識結果（server → client）

`{"state":"result", ...}` 的重要欄位：

| 欄位 | 說明 |
|------|------|
| `isFinish` | `false` = partial（暫定，會被覆蓋）；`true` = final（一個 utterance 的最終結果） |
| `recog_result` | 辨識文字 |
| `recog_index` | 結果序號 |
| `sen_start_ms` / `sen_end_ms` | 該句在原始音訊時間軸的位置（毫秒） |
| `recog_word` | 詞彙詳細資料（每個詞的時間、信心度），陣列 |
| `err_code` | `0` = OK，其他見下表 |

---

## 5. 錯誤碼（`err_code`）

| Code | 說明 | 處理建議 |
|------|------|----------|
| 0 | OK | — |
| -2 | 參數錯誤 | 檢查 start 訊息欄位 |
| -3 | 太長時間未送資料 | 檢查 PCM 是否真的有送、是否該 stop |
| -4 | 服務內部錯誤 | 重試 |
| -5 | Token 無效 | 檢查 token |
| -6 | 沒有辨識結果 | 該段沒人聲，正常情況 |
| -8 | 參數內容太長 | 檢查 start 訊息有無多餘欄位 |
| -9 | License 過期 | 通知賽微 |
| -10 | 伺服器連線逾時 | 重連 |
| -11 | 超過 License 上限 | 通知賽微增量 |

---

## 6. 對應到本系統的實作

### 連線拓撲

| 我們的元件 | role | 何時開連線 | 何時關連線 |
|-----------|------|-----------|-----------|
| `amidaemon.py` `fifo_stt_reader`（caller） | `caller` | 收到 `STT_CALL_START` UserEvent | FIFO EOF / caller channel Hangup |
| `amidaemon.py` `fifo_stt_reader`（agent） | `agent` | 收到 `STT_AGENT_START` UserEvent | FIFO EOF / agent channel Hangup |

**同一通電話的 caller 與 agent 是兩條獨立連線**，共用同一個 `${MYUUID}`。可在 server 端或我們前端用 UUID 把雙方逐字稿合併成完整對話記錄。

### 設計決定（根據賽微協定特性）

| 決定 | 為什麼 |
|------|------|
| **每通電話一條 ws 連線**（不是每段語音一條） | `bIsContinueRecog=true` 支援，省掉 handshake 成本，partial 也能即時收 |
| **打開 partial（`isGetPartial=true`）** | 邊講邊出字才是真正低延遲，是這次改造的主要動機 |
| **拔掉 client 端 Silero VAD** | server 已經 EPD，本地 VAD 多餘且會拖延延遲 |
| **直接送 8kHz s16le，不 resample** | 賽微協定原生支援，省掉 CPU |
| **每幀 100ms**（建議值，可調） | 8kHz × 2 byte × 0.1s = **1600 bytes / frame**，平衡延遲與 frame overhead |

### 與舊流程的差異

| 舊流程 | 新流程 |
|--------|--------|
| FIFO 讀 PCM → 本地 Silero VAD → 切 WAV 段 → 存檔 → HTTP POST | FIFO 讀 PCM → 直接 ws.send_binary() |
| WAV 檔保留在 `/home/sipuser/stt_bridge/<uuid>/<role>/` | 預設不存 WAV（如要保留 audit，可同時寫一份磁碟） |
| 一段語音 ~2 秒延遲才得到結果 | partial：~100~300ms；final：EPD 偵測完 + 推論時間 |
| 回傳：`{"text":"..."}` | 回傳：partial（多次）+ final（一次/utterance） |

---

## 7. 斷線與重試

官方建議（§9.2）：通話過程中若連線中斷，應記錄狀態並在合理次數內重連。

我們系統的策略（暫定）：
- **通話中連線斷掉**：殘餘音訊已丟失，不重連，把錯誤 log 起來即可（可考慮 fallback 到舊的 HTTP 流程）
- **start 失敗**（連不上、token 錯）：log + 通知，這通電話不做 STT 但通話照常進行

---

## 8. 還沒處理的事

- **AI Server 對話端**（`audiosocketv9000.py` → `192.168.5.114:5000`）**不在這次改造範圍**。AI Server 是另一個服務，協定也不同（目前還是 HTTP POST WAV 檔），之後若要 streaming 化是另一個議題。
- **TTS**：賽微的 TTS 走 gRPC（見 `台研科技/202603101/TTS_gRPC/`），未來可替換現行的 `Playback(${AI_TTS_FILE})` 為 streaming TTS，但這是後話。
- **逐字稿後送處**：partial / final 收到之後要丟去哪裡？（前端字幕 server？log file？SQLite？）目前先 print，等使用情境定下來再接。

### 延遲調優（已知問題）

| 現象 | 原因 | 調整方式 |
|------|------|----------|
| Final 結果慢 0.4~1s | EPD 偵測靜音後才出 final；預設 `epdFrameNum=80`（0.8s） | 調小 `epd_frame_num`，例如 `40`（0.4s），風險：容易把一句話切兩段 |
| Partial 延遲高 | FIFO 的 `f.read(1600)` 在 pipe 上可能返回碎小 chunk（10~20ms），造成大量小封包 | 在 `fifo_stt_reader` 加累積 buffer，湊滿 1600 bytes 再送 |
| 第一個 partial 慢 | WebSocket 連線建立 + server 回 `listening` + 送 `start` 有一次性 handshake overhead | 正常，約 100~300ms，無法消除 |

---

## 9. 參考實作

- 標準 client class：[ai/stt/cyberon_stt_client.py](../ai/stt/cyberon_stt_client.py)（含 CLI 測試模式，可餵 WAV 檔）
- amidaemon 整合版：[ai/ami/amidaemon_cyberon.py](../ai/ami/amidaemon_cyberon.py)（已驗證可啟動、連上 AMI、接通話）

### amidaemon_cyberon.py 整合細節

| 項目 | 說明 |
|------|------|
| 載入方式 | `importlib.util.spec_from_file_location` 直接指定 `ai/stt/cyberon_stt_client.py` 路徑，避免 Pylance 自動插入 import 破壞執行順序 |
| Linter 設定 | 根目錄 `pyrightconfig.json` 加入 `extraPaths: ["ai/stt"]`，讓 Pylance 能解析 `cyberon_stt_client` |
| FIFO → STT | 每通電話開兩條 WebSocket（caller / agent），MixMonitor 的 `r()` 方向各自接一條 |
| chunk 大小 | `PCM_CHUNK_BYTES = 1600`（100ms @ 8kHz s16le） |
| 啟動確認 | `python3 -u ai/ami/amidaemon_cyberon.py` 輸出：`✅ AMI 登入成功` + `👂 等待 AudioSocket END_SPEECH 通知` |

---

## 10. CLI 測試方法（已驗證可用）

### 前置：WAV 格式轉換

Asterisk 錄音預設存 **G.711 A-law**（format code 6），Python `wave` 模組不支援。  
測試前需轉成 **Linear PCM s16le**：

```bash
ffmpeg -i input.wav -acodec pcm_s16le input_pcm.wav
```

### 執行 CLI 測試

```bash
python3 ai/stt/cyberon_stt_client.py \
    path/to/test.wav \
    --url ws://192.168.5.204:8890/SttProxy/recognition \
    --token yGFJ1D5HcTXaysVMc_GEfQto-lWfjjgruiqdAhEh0fw4YKbJ1TkYFYOOQ80nuXuvimoRqtzr99iqXE8KN5_Cxj6ksGV1uVLilv1ERRp0Je_B4MhASKJmX5evRd0VMdPf \
    --realtime
```

- `--realtime`：模擬即時串流（依音訊長度 sleep，更貼近真實通話）
- 拿掉 `--realtime`：一次衝完（測試用，速度更快）
- `--no-partial`：只看 final 結果，不顯示 partial 中間字

### 注意事項

- STT Server 不走 SSL，直接用 `ws://`（不是 `wss://`）
- Token 直接放在 `--token` 參數，CLI 會帶進 start 訊息
- WAV 必須是 **mono**、**s16le**、**8000 或 16000 Hz**
