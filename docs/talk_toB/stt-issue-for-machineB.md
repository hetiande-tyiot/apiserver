# STT 問題回報（機器A → 機器B）

> 日期：2026-05-10

---

## 問題一：STT FINAL 結果延遲過長

### 現象

報警人說完一句話（約 3 秒），到 amidaemon 收到 STT FINAL 結果，中間等待約 **6～7 秒**。整體通話體驗（說完話到聽到 AI 回覆）超過 **9～10 秒**，不符合 110 緊急受理需求。

### 分析

- amidaemon 透過 WebSocket 送 `start` 時帶入 `epdFrameNum=10`（原為預設 40），但延遲完全沒有改變
- 推測 Cyberon STT Server 有**伺服器端 EPD 設定**，覆蓋或忽略了客戶端傳入的 `epdFrameNum`
- 根據實測，目前伺服器端 EPD 靜音超時約為 **6～7 秒**

### 請機器 B 協助

請確認 Cyberon STT Server 的 EPD 靜音超時設定，並調整為 **0.8～1.5 秒**。

目標：報警人說完話後 **1 秒內**出現 FINAL 結果。

---

## 問題二：一句話被拆成多段 FINAL

### 現象

報警人說一句完整的話（例如「新北市新莊區中正路發生車禍」），Cyberon STT 在句中的自然停頓點提前觸發 FINAL，將同一句話拆成 2～3 段送出，導致 NLP 只收到片段文字、無法正確判斷案件。

### 目前的臨時解法

機器 A 在 amidaemon 加入 **2 秒 debounce buffer**：收到 FINAL 後等待 2 秒，若有追加 FINAL 則合併，再一起送 NLP。此做法會多加 2 秒延遲。

### 請機器 B 協助

如果 Cyberon STT Server 的 EPD 可以設定「在短暫停頓不要觸發 FINAL，只在較長靜音才觸發」，請提供建議設定值，讓機器 A 可以縮短或移除 debounce buffer。

---

## 參考：機器 A 送出的 WebSocket start 訊息

```json
{
  "action": "start",
  "domain": "freeSTT-zh-TW",
  "platform": "asterisk",
  "uid": "<uuid8>-caller",
  "type": "audio/L16; rate=8000",
  "token": "<token>",
  "isGetPartial": true,
  "bIsDoEPD": true,
  "bIsContinueRecog": true,
  "epdFrameNum": 10
}
```

音訊格式：**8kHz, 16-bit signed PCM, mono**，每次送出 1600 bytes（100ms）。

---

## 備選方案（若 Server 端無法調整）

若機器 B 無法修改 Cyberon STT Server 設定，機器 A 可改為：

- 停用 Server EPD（`bIsDoEPD: false`）
- 機器 A 在 amidaemon 自行計算 PCM 音量（RMS），偵測靜音後主動送 `stop` 給 STT Server，強制取得 FINAL

此方案不需要機器 B 改動，但需要機器 A 開發，請機器 B 確認 `bIsDoEPD: false` + 主動送 `stop` 的行為是否符合預期（Server 是否會在收到 stop 後正確輸出當前累積的辨識結果）。
