# TTS API 調查請求（給機器 B）

## 背景

機器 A 這邊的 STT → 110LLM → MySQL 整條管線已經打通。
下一步要接 Cyberon TTS，把 110LLM 受理員的回覆文字轉成語音播給報警人聽。

目前已知 STT 端點：
```
ws://192.168.5.204:8890/SttProxy/recognition
```

TTS 應該也在同一台 Cyberon 伺服器上，但端點未知。

---

## 請幫我查以下資訊

### 1. TTS 端點 URL
類似 STT 的格式，可能長這樣：
```
ws://192.168.5.204:xxxx/TtsProxy/...
http://192.168.5.204:xxxx/...
```

### 2. 輸入格式
- 傳文字的方式：HTTP POST？WebSocket？
- Request body 格式？（JSON？純文字？）
- 範例：
```json
{"text": "新北市警局 110 您好，請問哪裡發生什麼事？"}
```

### 3. 輸出格式
- 回傳的是什麼？
  - WAV 檔案的二進位內容（binary）？
  - PCM 原始音訊串流？
  - 音檔路徑？
- 取樣率：8000Hz？16000Hz？
- 位元深度：s16le？

### 4. 有沒有現成測試範例或文件
如果有 client 程式或 SDK 文件更好，直接附上。

---

## 查找方式建議

```bash
# 查 Cyberon 相關設定或文件
find / -name "*.md" -o -name "*.txt" -o -name "*.pdf" -o -name "*.json" \
  2>/dev/null | xargs grep -li "tts\|TTS\|synthesis\|合成" 2>/dev/null | head -20

# 查目前有哪些 port 在監聽
ss -tlnp

# 查是否有 TTS 相關 Python 程式
find / -name "*.py" 2>/dev/null | xargs grep -li "tts\|TTS" 2>/dev/null \
  | grep -v __pycache__ | head -20

# 查 Cyberon 相關目錄
find / -path "*cyberon*" -o -path "*Cyberon*" 2>/dev/null | head -30
```

---

## 回傳格式

請把結果整理後寫成一份 md 檔，命名加上 `-for-machineA`，方便機器 A 這邊讀取。
