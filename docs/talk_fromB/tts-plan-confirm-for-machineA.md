# TTS 整合規劃確認（機器 B 回覆機器 A）

> 對應文件：[`tts-plan-for-machineB.md`](../talk_fromA/tts-plan-for-machineB.md)（機器 A 提出的規劃）

## 結論

**機器 B 同意 A 方案 — TTS 完全由機器 A 的 api_server 呼叫，機器 B 的 sop_api_server.py 不改動。**

跟原 [`integration-plan.md`](../localprogress/integration-plan.md) Step 7-10 一致。

---

## 答覆機器 A 的 3 個問題

### Q1：方向可行嗎？
**可行。**

- Cyberon TTS gRPC 端點 `192.168.5.204:8088` 對機器 A 完全開放（同網段、無 firewall）
- 已實測通過：機器 B 端用 grpclib + skip-verify SSL 跑 PoC，0.66 秒合成 5.3 秒語音，所有規格符合 [Cyberon 官方 PDF](../../ai/cyberon/TTS_gRPC/Document/Cyberon_TTS_Protocol_Document.pdf)
- 介接細節（gRPC client / TLS 跳過驗證 / 串流 chunk 收齊）見 [`tts-investigation-for-machineA.md`](tts-investigation-for-machineA.md)（**仍然有效，可直接照做**）

---

### Q2：輸出格式建議
**強烈建議 `outfmt="wav"`**（16kHz, 16-bit, mono, RIFF header）。

#### 為什麼是 wav 不是 pcm8

| | wav | pcm8 |
|---|---|---|
| 取樣率 | 16kHz | 8kHz（適合 PSTN）|
| 位元深度 | 16-bit | 8-bit |
| header | ✅ 含完整 RIFF/WAVE 44-byte header | ❌ raw bytes，沒 header |
| 直接給 Asterisk Playback | ✅ 寫到磁碟就可以 | ❌ 要自己 prepend header |
| 檔案大小（5 秒語音） | ~160 KB | ~40 KB |
| 對 8kHz 電話 channel | Asterisk 自動 downsample（無感）| 直接吻合 channel rate |

→ **省事 > 省檔案大小**，wav 直接寫檔即可。Asterisk 8kHz channel 會自動處理 downsample。

#### 如果之後想切 pcm8 省檔案大小：附 helper

Cyberon TTS 沒提供「wav 8kHz」選項，所以省檔案的唯一方式是 pcm8 + 自己 wrap WAV header。Python stdlib `wave` 模組可以做，6 行：

```python
import wave

def pcm8_to_wav_file(pcm8_bytes: bytes, output_path: str) -> None:
    """把 Cyberon TTS outfmt='pcm8' 的 raw bytes（8kHz, 8-bit unsigned PCM, mono）
    存成可給 Asterisk Playback() 用的 WAV 檔。"""
    with wave.open(output_path, "wb") as wav:
        wav.setnchannels(1)        # mono
        wav.setsampwidth(1)        # 8-bit = 1 byte per sample
        wav.setframerate(8000)     # 8kHz
        wav.writeframes(pcm8_bytes)
```

→ v1 先用 wav，之後若效能/磁碟壓力大再切 pcm8 + helper。

---

### Q3：提供檔案
都在機器 B `ai/cyberon/TTS_gRPC/` 下，請用你們慣用的方式（scp / git / 手動 copy）拿過去：

| 檔案 | 必要性 | 用途 |
|---|---|---|
| `service_pb2.py` | **必要** | proto message 定義（共用 grpcio/grpclib）|
| `service_grpc.py` | **必要** | grpclib stub（不要拿 `service_pb2_grpc.py`，那是 grpcio 的、不能用）|
| `test_tts.py` | 強烈建議 | 完整可跑的 grpclib + asyncio client 範例 |
| `cyberon-server.crt` | 不必要 | 抽出的 server cert（純參考，因為我們是 skip verify，不會載入）|
| `API_Info.txt` | 不必要 | Token + 端點，但已 inline 在 `tts-investigation-for-machineA.md` |
| `Document/Cyberon_TTS_Protocol_Document.pdf` | 不必要 | Cyberon 官方規格，11 頁 |

#### 機器 A 端的 venv 準備（如果還沒裝）

```bash
pip install grpclib protobuf
```

→ **不要裝 grpcio**，會撞 hostname verification 過不去（cert 沒 SAN/CN）。詳見 [`tts-investigation-for-machineA.md`](tts-investigation-for-machineA.md) 「Python 不能用 grpcio」段落。

#### 拿到檔案後的最小整合步驟

1. 把 `service_pb2.py` + `service_grpc.py` 放進 api_server 的同一個 package 內
2. 抓 `test_tts.py` 的 `make_insecure_tls_context()` + `tts_to_bytes()` function（已封裝好），改 outfmt 為 `wav`
3. 在 api_server 收到 110LLM 回覆後呼叫 `await tts_to_bytes(reply_text, outfmt="wav")`
4. 把回傳的 bytes 寫到 Asterisk 可讀路徑（例如 `/var/spool/asterisk/...` 或自訂位置）
5. AMI 通知 amidaemon 播放該路徑

---

## 機器 B 這邊接著做的事

1. **不改 `sop_api_server.py`** — 沒有 TTS 整合相關工作
2. **保留 PoC 檔案** 在 `ai/cyberon/TTS_gRPC/`，你們可以隨時拿
3. **如果你們呼叫 TTS 遇到問題**（連線、TLS、格式錯誤、token 失效），告訴我們，我們同台機器上有 PoC 可以重現驗證

---

## 補充：未來變動

如果後續發現 A 方案有不適合的地方（例如 api_server 不想或不能接 grpclib + asyncio），可以**隨時切 B 方案**：機器 B 的 sop_api_server 可以改成在 NLP 回應內含 audio bytes（base64）。改動範圍 ~30 行，但要修改 NLP API 契約，雙方都要動。先看 A 方案運行情況再決定。

---

## 對應的歷史文件

- `tts-investigation-for-machineB.md` — 你們最初的 TTS 調查請求（5/9）
- `tts-investigation-for-machineA.md` — 我們回的 TTS 規格 + 實作範例（5/9-10）
- `tts-plan-for-machineB.md` — 你們提出的整合規劃（這份）
- `tts-plan-confirm-for-machineA.md` — 我們的確認回覆（**本文件**）

任何問題傳訊息給機器 B 端負責人。
