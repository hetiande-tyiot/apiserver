# 110LLM API 介接指引（給機器 A 開發者）

> 此文件目的：機器 A 不必看機器 B 的程式碼或部署細節，就能正確介接這個 API。

## 服務概觀

機器 B 提供一個 HTTP API，把報案人的 STT 文字轉換成「110 受理員」的應答，並在通話結束時回傳一份結構化案件 JSON。

- **Server URL**：`http://<MACHINE_B_IP>:8100`（請向部署者索取實際 IP）
- **協定**：HTTP/JSON，無認證（內網 only）
- **請求/回應**：UTF-8，請求 body 用 `Content-Type: application/json`
- **內部運作**：TW-110-Bert 分類器 + SOP 流程引擎，每個 session 一條獨立背景 thread

---

## 一通電話的標準流程

```
[電話接起]
    │
    ├─→ POST /session/new
    │       回 session_id + 受理員開場白
    │
[每句 STT FINAL]
    │
    ├─→ POST /session/{id}/input
    │       回 受理員回覆（可能多句）+ done 旗標
    │       重複以上直到 done=true 或通話結束
    │
[電話掛斷 / 流程結束]
    │
    ├─→ POST /session/{id}/hangup        （主動掛斷時呼叫）
    │       回 done=true
    │
    └─→ GET  /session/{id}/result
            回 結構化案件 JSON，給後台寫報案紀錄
```

關鍵原則：
- **`outputs` 全部要語音播放給報案人聽**（不論 done 與 error 為何）。
- **`done: true` 表示 SopEngine 已結束**，不要再 push input（會回 409）。
- **通話結束後一定要呼叫 `GET /result`** 拿案件資料，不然這通電話的對話跟分類結果就丟了。

---

## API 端點規格

### POST /session/new

建立新通話 session。

**請求**：無 body

**回應 (200)**：
```json
{
  "session_id": "fe3dd259-370d-46ae-9658-d205454f63d7",
  "outputs": ["新北市警局 110 您好，請問哪裡發生什麼事？"],
  "done": false
}
```

**curl**：
```bash
curl -s -X POST http://<MACHINE_B_IP>:8100/session/new
```

**注意**：
- `outputs` 是受理員開場白，請語音播放給報案人聽。
- 同時會在伺服器端啟動一條背景 thread 跑 SopEngine。
- 第一次呼叫整體響應約 1–3 秒（要等 engine 跑開場白）。

---

### POST /session/{session_id}/input

把一句 STT FINAL 文字推進 session，拿受理員回覆。

**請求**：
```json
{ "text": "板橋發生車禍，有人受傷" }
```

**回應 (200)**：
```json
{
  "outputs": ["請問現場有多少人受傷？傷勢如何？有沒有流血、骨折或昏迷的情況？"],
  "done": false,
  "error": null
}
```

**curl**：
```bash
curl -s -X POST http://<MACHINE_B_IP>:8100/session/$SID/input \
  -H "Content-Type: application/json" \
  -d '{"text":"板橋發生車禍，有人受傷"}'
```

**錯誤 HTTP code**：
- `404`：session_id 不存在（從沒建過、或 server 重啟過）
- `409`：session 已結束（done=true），不能再推 input

**注意**：
- `outputs` 可能是 0 句到多句，全部依序播放。
- 這個 endpoint 內部會等 engine 把這輪 say() 全部跑完才回，所以收到回應就表示「受理員這輪講完了」。
- 預期回應時間：通常 < 1 秒，但 BERT 分類最多會花 3–5 秒。
- `done: true` 時請看下方「`error` 欄位語意」，那是業務性結束的訊號。

---

### POST /session/{session_id}/hangup

報案人掛斷時呼叫，結束 session。

**請求**：無 body

**回應 (200)**：
```json
{ "done": true }
```

**curl**：
```bash
curl -s -X POST http://<MACHINE_B_IP>:8100/session/$SID/hangup
```

**錯誤 HTTP code**：
- `404`：session_id 不存在

**注意**：
- 對已 done=true 的 session 再呼叫 hangup 不會錯誤（idempotent），但意義不大。
- 內部送 `None` 進 engine 的 input queue，engine 拋 `EndOfInput` 後優雅結束。
- 預期 5 秒內回應。

---

### GET /session/{session_id}/result

取得這通電話的結構化案件 JSON。**通話結束後（done=true）才呼叫**。

**請求**：無 body

**回應 (200) — session 還沒結束**：
```json
{ "done": false }
```

**回應 (200) — session 已結束**：
```json
{
  "done": true,
  "error": "caller_hangup",
  "case": {
    "act_sub_class": "車禍A2類(受傷)",
    "incident_desc": "板橋發生車禍，有人受傷",
    "location": null,
    "location_hint": "板橋發生車禍，有人受傷",
    "injured": true,
    "transcript": [
      { "role": "assistant", "text": "新北市警局 110 您好..." },
      { "role": "caller",    "text": "板橋發生車禍，有人受傷" }
    ]
  }
}
```

**curl**：
```bash
curl -s http://<MACHINE_B_IP>:8100/session/$SID/result
```

**錯誤 HTTP code**：
- `404`：session_id 不存在

**注意**：
- 即使流程沒走完（被掛斷或業務 fallback），`case` 還是會有部分欄位。**保證至少包含**：`act_sub_class`（分類結果）、`incident_desc`（首句）、`transcript`（完整對話）。
- 案件 JSON 有 130+ 個欄位，大部分通話只會填到 5–15 個，其餘是 `null`，請容忍稀疏 dict。
- 詳細結構見下方「Case JSON 結構」。

---

### GET /health

健康檢查。

**回應 (200)**：
```json
{ "status": "ok", "sessions": 3 }
```
- `sessions`：目前活著的 session 數（含已結束尚未清掉的）

**curl**：
```bash
curl -s http://<MACHINE_B_IP>:8100/health
```

---

### GET /session/{session_id}/output（極少需要）

無阻塞地拿目前 outputs queue 內的所有訊息（不推 input）。通常用不到，因為 `/input` 已經把 outputs drain 過。只有在 race condition 或 polling 模式下才考慮。

**回應 (200)**：
```json
{ "outputs": [...], "done": false }
```

---

## 最重要：`error` 欄位的語意

**`error` 不是 HTTP 錯誤**。它是 SopEngine 結束的「原因標記」。**有值不一定代表技術錯誤**，常見情況下大多是「業務性結束」。

| `error` 值 | 意思 | 機器 A 該怎麼處理 |
|---|---|---|
| `null` | 流程進行中 OR 完整跑完 | 正常處理 outputs |
| `"caller_hangup"` | 機器 A 呼叫了 hangup，engine 收到 EOF | **預期行為**，正常結束通話 |
| 含「轉接專人」字樣的訊息 | SOP 業務上判斷無法繼續，主動 fallback | **預期行為**，把 outputs 念給報案人後轉接人工 |
| 其他字串（例如 stack trace、`ValueError`、`KeyError` 等） | 真正的技術錯誤 | 通報機器 B 維運，**不要 retry**（同樣 input 會再撞同樣 bug） |

### 為什麼 error 跟 outputs 會出現同一段文字

當 SOP 流程走 fallback「轉接專人」時，伺服器內部先把這段文字推進 outputs queue（讓報案人聽到），然後再 raise 一個 `TransferToHuman` exception 結束流程。這個 exception 的 message 就被寫進了 `error` 欄位。所以你會看到 outputs 跟 error 是同一段文字，這是正常的。

### 實作建議（Python 範例）

```python
def is_business_end(error: str | None) -> bool:
    """是否為業務性結束（不是技術錯誤）"""
    if error is None:
        return False
    if error == "caller_hangup":
        return True
    if "轉接專人" in error:
        return True
    return False

# 收到 input 或 hangup 回應後...
if response["done"]:
    error = response.get("error")
    if is_business_end(error):
        # 業務結束：把 outputs 念完、正常結束通話、再 GET /result
        for text in response["outputs"]:
            tts_play(text)
        case = http_get(f"/session/{sid}/result")["case"]
        save_case_to_db(case)
        end_call_normally()
    elif error:
        # 技術錯誤：告警、不要 retry
        alert_devops(f"NLP server error: {error}")
        end_call_with_apology()
    else:
        # done=true 但沒 error：正常完成，照業務結束流程處理
        ...
```

---

## Case JSON 結構

### 核心欄位（任何案件都會有）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `act_sub_class` | str | BERT 分類結果，例如 `"車禍A2類(受傷)"`、`"詐騙"`、`"竊盜"` 等 80+ 類 |
| `case_summary` | str/null | LLM 生成摘要（**目前未啟用 LLM，永遠 null**） |
| `incident_desc` | str | 報案人的首句陳述（用於分類與存檔） |
| `location` | str/null | 確認過的精確地點（區/路/門牌） |
| `location_hint` | str/null | 從首句粗抽取的地點線索（不一定完整） |
| `caller_name_hint` | str/null | 報案人姓氏線索（如「我姓林」） |
| `caller_phone` | str/null | 從文字抽取的電話 |
| `injured` | bool/null | 現場是否有人受傷 |
| `need_ambulance` | bool/null | 是否需要救護車 |
| `safety_risk` | str/null | 安全風險描述 |
| `transcript` | list | **完整對話歷史**，格式 `[{"role": "assistant"\|"caller", "text": "..."}, ...]` |

### 案件類型專屬欄位

每個案件子類別有專屬欄位群，命名規則 `<類別>_<項目>`。例如：

| 類別 | 主要欄位 |
|---|---|
| 車禍 A2（受傷） | `a2_injury_status`、`a2_traffic_impact`、`a2_hit_and_run` |
| 車禍 A3（財損） | `a3_injury_confirmation`、`a3_scene_safety`、`a3_hit_and_run` |
| 詐騙 | `fraud_golden_window`、`fraud_account_info`、`fraud_method` 等 |
| 竊盜 | `theft_location_precise`、`theft_property_loss` 等 |
| 醫療急救 | `medical_consciousness_breathing`、`medical_history` 等 |
| 違規停車 | `parking_location_precise`、`parking_violation_style` 等 |

完整欄位清單見機器 B 端的 `case_info.py`，目前共 130+ 欄位。**大部分通話只會填 5–15 個**，其餘為 null，請容忍。

---

## 端到端測試（在機器 A 端跑這段確認連線）

```bash
# 0. 設定 server URL
SERVER=http://<MACHINE_B_IP>:8100

# 1. health check
curl -s $SERVER/health
# 預期: {"status":"ok","sessions":...}

# 2. 開 session
RESP=$(curl -s -X POST $SERVER/session/new)
SID=$(echo "$RESP" | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
echo "$RESP" | python -m json.tool --no-ensure-ascii
echo "SID=$SID"
# 預期: outputs 含開場白「新北市警局 110...」

# 3. 推第一句（觸發分類）
curl -s -X POST $SERVER/session/$SID/input \
  -H "Content-Type: application/json" \
  -d '{"text":"板橋發生車禍，有人受傷"}' | python -m json.tool --no-ensure-ascii
# 預期: outputs 含追問「請問現場有多少人受傷？...」

# 4. 推第二句
curl -s -X POST $SERVER/session/$SID/input \
  -H "Content-Type: application/json" \
  -d '{"text":"兩個人輕傷"}' | python -m json.tool --no-ensure-ascii

# 5. 掛斷
curl -s -X POST $SERVER/session/$SID/hangup | python -m json.tool --no-ensure-ascii
# 預期: {"done": true}

# 6. 取結果
curl -s $SERVER/session/$SID/result | python -m json.tool --no-ensure-ascii
# 預期: case 內含 act_sub_class="車禍A2類(受傷)"、incident_desc、transcript
```

如果上面 6 步全跑通，介接環境就 OK 了。

---

## FAQ

### Q: session_id 會過期嗎？
A: 沒有 TTL。但 server 重啟後所有 session 會清空，再呼叫會 404。重大失敗請告警。

### Q: 同一通電話最多可以推幾句 input？
A: 沒有上限。但 SopEngine 內有 reask 計數，連續多次「答非所問」會走 fallback「轉接專人」。

### Q: input 的回覆 timeout 是多久？建議 HTTP timeout 設多少？
A: server 端 `_drain_output` 預設等 5 秒（含 BERT 分類時間）。建議機器 A 端 HTTP timeout 設 **10 秒**。

### Q: outputs 會回多句嗎？順序怎麼處理？
A: 會。SopEngine 一輪可能 say() 多次（例如「請稍等」+「請問...」），全部累積在同一個 outputs list。**請依 list 順序播放**。

### Q: 如果 outputs 是空 list 怎麼辦？
A: 表示這輪 engine 沒有要說話（罕見，可能是 race condition 或處理中）。可以等下一句 input；或呼叫 `GET /output` 輪詢殘留訊息。

### Q: 同時可以開幾個 session？
A: 沒有顯式上限。每個 session 是獨立 thread + queue，BERT 分類器是全域共用（不會每次重新載入）。RAM 是限制，預估每個 session 約 10–20MB。

### Q: 我可以用同一個 session_id 跨多通電話嗎？
A: **不行**。一個 session = 一通電話。每通電話都要重新 `POST /session/new`。

### Q: server 重啟後我之前的 session 怎麼辦？
A: 全部丟掉，沒有持久化。建議機器 A 自己保存 GET /result 的回應做後備。

### Q: BERT 分類錯了怎麼辦？
A: 模型推論不保證 100% 正確。`act_sub_class` 是「目前最佳猜測」，後續流程會根據它走，但 case 裡的其他欄位還是會靠對話確認。如果分類錯誤率高，請回報機器 B 維運。

### Q: server 沒回應 / connection refused 怎麼辦？
A: 先 `curl /health`，沒回就是 server 沒起來，聯絡機器 B 維運。

### Q: 我要不要在每次通話結束後 cleanup（例如刪掉 session）？
A: 不需要。server 端目前沒有顯式刪除 endpoint，session 用完後留在記憶體（直到 server 重啟）。GET /result 拿完就可以了。

---

## 已知限制（v1.0）

1. **無認證**：純 HTTP，靠內網隔離。如果未來要跨網段，要加認證機制。
2. **無持久化**：server 重啟丟所有 session。case 資料要靠機器 A 端 GET /result 後自己存。
3. **單機**：沒做多 instance / load balancing。同時 session 量有上限（依 RAM）。
4. **LLM 未啟用**：`case_summary` 永遠 null；某些欄位的抽取準確度低於有 LLM 時。

---

## 聯絡

- **API 規格疑問 / bug report**：請描述復現步驟、附上 session_id、附上 GET /result 拿到的 transcript
- **服務中斷 / health 不通**：聯絡機器 B 維運
