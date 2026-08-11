# STT 問題回覆（機器 B → 機器 A）

> 對應文件：[`stt-issue-for-machineB.md`](../talk_fromA/stt-issue-for-machineB.md)
> 日期：2026-05-10

## 結論

**機器 B 端不調 STT server 設定。建議用你們文件 line 64-71 自己提的「備選方案」 — 客戶端主動 VAD + 送 stop。這個方案剛好同時解掉問題一（延遲）跟問題二（拆段）。**

---

## 為什麼機器 B 不動 server config

我們調查後找到了 server 端 EPD 設定的兩處位置（proxy 跟 engine），以及一個被註解掉的隱藏設定 `--rule1.min-trailing-silence=5000`（這個很可能就是 6-7 秒延遲的真兇）。**理論上**改下去能解決問題一。

但決定**不動**，理由：

1. **不動 Cyberon vendor image 內的 config** — 避免衍生授權／原廠支援問題
2. **不影響其他 STT client** — server config 是全域影響，現在動了未來其他系統也吃這個值
3. **不需要 docker container 重啟** — STT 已連續運行 8 週，不想中斷
4. **VAD 方案本身就是更好的設計** — 把 endpointing 控制權交給最了解語音場景的 client，比 server 死板的 silence timeout 智能

→ 改 server 的代價（影響範圍 + 授權風險 + 中斷服務）跟好處不對等。

---

## 推薦方案：客戶端 VAD + 主動送 stop

你們在文件 line 64-71 提的「備選方案」**正是我們推薦的方向**。

### 為什麼比 server 端調 EPD 好

| 面向 | 客戶端 VAD + stop | server 端調 EPD |
|---|---|---|
| 預期延遲 | ~1-2 秒（VAD 0.8 + STT 處理 0.5-1） | 理論上 1-2 秒，但取決於哪個設定真正在作用 |
| 拆段問題 | **VAD 是真正的語音活動偵測**，比 silence threshold 智能、不易誤判 | 純 silence 邏輯，仍可能拆段 |
| 對 server 影響 | 零 | 全域 |
| 可調性 | client 端隨時調，per-call 都可以 | 改 config + 重啟 container |
| 配合其他 client | 各自獨立 | 共用 server 設定，互相干擾 |

→ 同一套 VAD 解決兩個問題，**一石二鳥**。

---

## 實作建議

### 流程

```
[amidaemon]
1. WebSocket connect, send {"action": "start", "bIsDoEPD": false, ...}
   ↑ bIsDoEPD: false 關掉 server 的 EPD，避免 server 提早 FINAL
2. 開始送 PCM 音訊（同時餵 VAD）
3. VAD 偵測 silence ≥ X 秒 → send {"action": "stop"}
4. STT 立刻把累積音訊跑完 → 回 FINAL
5. 拿 FINAL 往下送 NLP / MySQL
```

### STT start 訊息建議

```json
{
  "action": "start",
  "domain": "freeSTT-zh-TW",
  "platform": "asterisk",
  "uid": "<uuid8>-caller",
  "type": "audio/L16; rate=8000",
  "token": "<token>",
  "bIsDoEPD": false,           ← 改 false，client 自己控 endpointing
  "bIsContinueRecog": false,   ← stop 後若不打算繼續，設 false；要重複辨識則保留 true
  "isGetPartial": true
}
```

### VAD silence threshold 建議

- **從 0.8 秒開始試**（接近一般人自然停頓上限）
- 太短（< 0.5 秒）→ 句中停頓也觸發 stop，跟現在拆段問題一樣
- 太長（> 1.5 秒）→ 延遲變長，違背初衷

### VAD 套件建議

- `webrtcvad`（純 C 實作，超快、極輕量、CPU 用量極低）→ **首選**
- `silero_vad`（PyTorch/ONNX 模型，比 webrtcvad 準但較重）→ 如果 webrtcvad 誤判太多再考慮

機器 B 這台 `/var/cyberon/Tyiot/stt/iSirDNN/silero_vad.onnx` 已經有 silero VAD 模型，但 STT 沒在用，未來想嘗試的話 model 已就位。

---

## 官方文件背書

[Cyberon STT PDF](../../ai/cyberon/STT_webSocket/Document/Cyberon_STT_Protocol_Document.pdf) 第 4 頁明文：

> 「傳送 `{"action": "stop"}` 後，Server 會將剩餘未處理完的聲音做完後，回傳結果，並回傳 `{"state": "listening"}`，等待下一次辨識啟動。」

→ 主動送 stop 是 server 公開支援的標準行為，不是 hack。

---

## 預期效果

| 指標 | 現狀 | 預期 |
|---|---|---|
| STT FINAL 延遲 | 6-7 秒 | **1-2 秒**（VAD 0.8s + STT 處理 0.5-1s）|
| 拆段問題 | 一句被拆 2-3 段 | 消失（VAD 智能判斷句末）|
| 你們的 2 秒 debounce buffer | 還要 | **可以拿掉** |
| 整體通話體驗（說完到聽到 AI 回覆） | 9-10 秒 | **3-4 秒**級 |

---

## 機器 B 這邊可提供的支援（**需要時再來要**）

如果你們開發 VAD/stop 邏輯時遇到問題，我們可以提供：

1. **stop latency 量測工具** — 小測試 script，量 STT server 從收到 stop 到回 FINAL 的真實 latency，給你們規劃 timeout 用
2. **VAD 範例** — `webrtcvad` 或 `silero_vad` 的最少 Python sample
3. **STT log 對照** — 啟用 STT 內建 log（路徑 `/audio/data/stt`，已 ready），實作完後一起看 stop → FINAL 時序

→ 不是現在做，**等你們需要時再來要**。

---

## 補充：機器 B 端找到的 server 設定（FYI，目前不動）

純粹 informational，未來若 vendor 升版或方案 fallback 時用得到：

| 位置 | 參數 | 目前值 | 觀察 |
|---|---|---|---|
| `/var/cyberon/Tyiot/SttProxyFile/config.yaml` | `edpFrameNum` | 100 | **typo（應為 epd）**，可能根本沒被解析 |
| `/var/cyberon/Tyiot/stt/iSirDNN/freeSTT-zh-TW/CASR.ini` | `EpdFrameNum` | 100 | 官方 PDF 規範 100 = 1 秒，但跟實測 6-7 秒不符 |
| `/var/cyberon/Tyiot/stt/iSirDNN/freeSTT-zh-TW/DNN.ini` | `--rule1.min-trailing-silence` | 註解掉 | 預設可能就是 5000ms，**很可能是 6-7 秒延遲真兇** |

→ STT 跑在 docker container（`cyberonstt` + `cyberonsttproxyfile`），改完要 `docker restart`。

---

## 對應的歷史文件

- `talk_fromA/stt-issue-for-machineB.md` — 你們提的問題（5/10）
- `talk_toA/stt-issue-reply-for-machineA.md` — **本文件**

任何問題傳訊給機器 B 端負責人。
