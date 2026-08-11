# TTS 整合規劃說明（給機器 B 確認）

## 背景

機器 A 這邊目前已完成：
- Asterisk → Cyberon STT → FINAL 文字
- 文字 → api_server → MySQL + 110LLM
- 110LLM 受理員回覆存入 MySQL

下一步要接 TTS，把受理員回覆播給報警人聽。

---

## 機器 A 提出的規劃

**由機器 A 的 api_server 直接呼叫 Cyberon TTS。**

```
api_server（機器A）收到 110LLM 回覆文字
    ↓
api_server 直接 gRPC 呼叫 Cyberon TTS（機器B :8088）
    ↓
api_server 收到 WAV 音訊 bytes
    ↓
api_server 存成 WAV 檔案到本機
    ↓
api_server 通知 amidaemon 播放路徑
    ↓
Asterisk Playback() 播給報警人
```

**機器 B 的 sop_api_server 不需要改動。**
TTS 完全由機器 A 主動去拿，機器 B 只需繼續提供 110LLM API。

---

## 想請機器 B 確認的事

1. **這個方向可行嗎？**
   機器 A 直接 gRPC 連 Cyberon TTS（192.168.5.204:8088），機器 B 不介入 TTS 流程。

2. **輸出格式建議用哪個？**
   - `wav`（16kHz, 16-bit）→ 存檔，Asterisk Playback() 播放
   - `pcm8`（8kHz, 8-bit）→ 需要機器 A 自己加 WAV header 才能存檔
   - 機器 B 建議哪個最省事？

3. **機器 A 需要的檔案**
   機器 A 需要以下三個檔案才能呼叫 TTS，請問可以提供嗎？
   - `service_pb2.py`（proto message 定義）
   - `service_grpc.py`（grpclib stub）
   - Python client 範例（grpclib + asyncio）

   （機器 B 之前提到這些檔案在 `/home/aitop4/nlp_cyberon_server/ai/cyberon/TTS_gRPC/`）

---

## 如果機器 B 有其他建議

如果認為 TTS 應該由機器 B 這邊處理（例如 sop_api_server 直接回傳音訊），也歡迎提出替代方案，說明優缺點，機器 A 這邊再評估。
