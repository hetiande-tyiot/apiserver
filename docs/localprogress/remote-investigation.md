# 遠端機器調查清單

> 這份文件是給在遠端機器（執行 110LLM + Cyberon 的那台）上工作的 Claude 用的。
> 目標：收集整合所需的所有技術細節，回傳給 Asterisk 機器端的開發者。

---

## 背景說明

我們正在建立一套完整的 110 接警 AI 系統，架構如下：

```
報警人 → Asterisk (192.168.5.x) → Cyberon STT → 文字
                                              ↓
                                   110LLM SopEngine → 回覆文字
                                              ↓                ↓
                                   Cyberon TTS → 音訊     MySQL 案件資料
                                              ↓
                                   Asterisk 播音給報警人
```

Asterisk 機器已完成：AMI daemon、Cyberon STT 串流。
**尚未完成：Cyberon TTS 整合、MySQL 儲存、110LLM API server。**

---

## 需要你調查的事項

### 1. Cyberon TTS API

**目的**：把 SopEngine 產生的回覆文字（例如「請問事故地點在哪裡？」）轉成音訊，讓 Asterisk 播放給報警人。

請找出以下資訊：

#### 1a. TTS 端點在哪裡？

```bash
# 查看 Cyberon 相關設定或文件
find / -name "*.md" -o -name "*.txt" -o -name "*.pdf" -o -name "*.json" -o -name "*.yaml" \
  2>/dev/null | xargs grep -li "tts\|TTS\|synthesis\|合成" 2>/dev/null | head -20

# 查看目前有哪些 port 在監聽
ss -tlnp | grep -E "8[0-9]{3}"
netstat -tlnp 2>/dev/null | grep -E "8[0-9]{3}"

# 查看 Cyberon 相關的 Python 程式
find / -name "*.py" 2>/dev/null | xargs grep -li "cyberon\|CyberonTTS\|tts.*proxy\|TtsProxy" 2>/dev/null | head -10

# 查看是否有 Cyberon SDK / 範例程式
find / -path "*/cyberon*" -o -path "*/Cyberon*" 2>/dev/null | head -30
```

#### 1b. TTS 輸出的音訊格式？

需要知道：
- 取回的音訊是 WAV 檔案路徑？還是 WebSocket 串流 PCM？
- 取樣率：8000Hz？16000Hz？
- 位元深度：s16le？

#### 1c. 有沒有現成的 TTS 測試程式？

```bash
# 找 TTS 相關測試程式或 client
find / -name "*.py" 2>/dev/null | xargs grep -li "tts\|TTS" 2>/dev/null \
  | grep -v "__pycache__" | grep -v ".pyc" | head -20
```

---

### 2. 110LLM 環境

**目的**：確認 sop_api_server.py 可以在這台機器上正確執行。

#### 2a. Python 環境

```bash
# Python 版本
python3 --version

# 虛擬環境是否存在
ls ~/*/bin/activate 2>/dev/null
ls /opt/*/bin/activate 2>/dev/null

# 目前啟用的 pip 環境
which pip3
pip3 list | grep -E "torch|transformers|numpy|scikit|fastapi|uvicorn"
```

#### 2b. 110LLM 程式碼位置

```bash
# 找 sop_110_assistant.py 在哪
find / -name "sop_110_assistant.py" 2>/dev/null

# 找 TW-110-Bert 模型在哪
find / -name "model.safetensors" 2>/dev/null
find / -name "label_encoder.pkl" 2>/dev/null
```

#### 2c. 目前是怎麼執行的？

```bash
# 目前有哪些 Python 程式在跑
ps aux | grep python

# 有沒有 systemd service
systemctl list-units --type=service | grep -i "llm\|sop\|110\|ai"

# 有沒有 screen / tmux session
screen -ls 2>/dev/null
tmux ls 2>/dev/null
```

#### 2d. 測試 SopEngine 可以跑起來嗎？

進到 110LLM_0426 目錄，執行以下測試：

```bash
cd <110LLM_0426 的路徑>

printf "板橋發生車禍，有人受傷\n" | python3 sop_110_assistant.py \
    --input-mode text \
    --model-dir ../TW-110-Bert-v4/checkpoint-83352 \
    --flows-dir ./flows \
    --llm none \
    --debug
```

如果跑得起來，記錄輸出結果。

---

### 3. MySQL 環境

**目的**：確認資料庫環境，之後用來儲存案件資料。

```bash
# MySQL 是否已安裝並運行
systemctl status mysql 2>/dev/null || systemctl status mysqld 2>/dev/null
mysql --version 2>/dev/null

# 如果有 MySQL，確認可以連線
mysql -u root -p -e "SHOW DATABASES;" 2>/dev/null
# 或
mysql -e "SHOW DATABASES;" 2>/dev/null
```

---

### 4. 網路連線

**目的**：確認兩台機器可以互相溝通。

```bash
# 這台機器的 IP
ip addr show | grep "inet " | grep -v "127.0.0.1"

# 能不能 ping 到 Asterisk 機器（192.168.5.x）
ping -c 3 192.168.5.114
```

---

## 回傳格式

請把以上每個指令的輸出整理後回傳，格式如下：

```
=== 1a. TTS 端點 ===
（指令輸出）

=== 1b. TTS 音訊格式 ===
（找到的資訊）

=== 2a. Python 環境 ===
（指令輸出）

...以此類推
```

如果某個指令找不到東西，就寫「未找到」即可。
