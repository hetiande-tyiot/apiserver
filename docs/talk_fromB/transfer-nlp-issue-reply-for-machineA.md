# 轉接真人後雙通道 NLP 需求回覆（機器 B → 機器 A）

> 對應文件：[`transfer-nlp-issue-for-machineB.md`](../talk_fromA/transfer-nlp-issue-for-machineB.md)
> 日期：2026-05-12

## 結論

**機器 B 新增 `POST /session/{id}/observe` endpoint 解決 bridge 後的雙通道對話接收。`/input` 行為完全不變**，現有 client 程式不必動。

---

## 需求對應

| 你們的需求 | 我們的解法 | 說明 |
|---|---|---|
| 1. `/input` 加 `role` 欄位 | **新增 `/observe` endpoint**（不動 `/input`）| 職責清楚：`/input` 對話模式、`/observe` bridge 模式 |
| 2. case_summary 持續更新 | `/observe` 內部呼叫 LLM extractor 以 **upgrade 模式**抽取 | 已抽到的精細版可覆寫粗略版（例 `location: "板橋"` → `"板橋區中山路 100 號"`） |
| 3. transcript 包含 agent | `/observe` 自動把 `{role, text}` append 到 transcript | role 直接用 `"agent"`（與 `caller` / `assistant` 並列） |

---

## API 規格：`POST /session/{session_id}/observe`

### 用途
Bridge 後（轉接真人後）使用。caller 跟 agent 的 STT FINAL 文字都送這個 endpoint，server 會：
- 把 `{role, text}` append 到 transcript
- **caller 發言時** 觸發 LLM extractor 重新抽取 case 欄位（upgrade 模式）
- 同步 rewrite 本機 case JSON log（含 bridge 期間的更新）

### Request
```json
POST /session/{session_id}/observe
Content-Type: application/json

{
  "text": "我們在板橋區中山路 100 號 3 樓",
  "role": "caller"
}
```

| 欄位 | 型別 | 必要 | 說明 |
|---|---|---|---|
| `text` | string | ✅ | STT 辨識文字 |
| `role` | string | ✅ | 必須是 `"caller"` 或 `"agent"`（其他值會 400） |

### Response
```json
{
  "updated_fields": ["location", "a2_traffic_impact"],
  "transcript_size": 8
}
```

| 欄位 | 說明 |
|---|---|
| `updated_fields` | 這次呼叫實際被 LLM 更新的 case 欄位名稱（不含 `transcript`）。空 list 表示 LLM 沒抽到新東西可更新。 |
| `transcript_size` | append 後 transcript 的總長度（含 assistant + caller + agent 全部） |

### Error codes
- `404` — session_id 不存在
- `409` — session 還沒 done。**bridge mode 只能在 SopEngine 結束後啟用**（`done=true` 才行）。AI 對話階段請繼續用 `/input`
- `400` — role 不是 `caller` 或 `agent`
- `500` — engine 狀態遺失（罕見，server 重啟過會發生）

### 注意事項

| 注意 | 說明 |
|---|---|
| **session 必須 done=true** | 否則回 409。bridge 通常發生在 transfer_to_human 後，那時 done 已 true ✓ |
| **agent 發言不觸發 LLM 抽取** | 受理員的釐清問句通常沒有新欄位資訊。但**會 append 到 transcript**，不會被忽略 |
| **caller 發言才觸發 LLM** | 每次 caller 說話都跑一次 LLM `_llm_post_extract`（依 act_sub_class 跑對應 schema/rules） |
| **upgrade mode** | 已抽到的可能被精準版覆寫；null 欄位會被填入 |
| **本機 case log 同步更新** | `/home/aitop4/nlp_cyberon_server/log/case_*.json` 會隨 `/observe` 被 rewrite，不必你們補 transcript |

### 預期延遲

- agent 發言：~10-50 ms（純 transcript append）
- caller 發言：~100-500 ms（含 LLM `_llm_post_extract`）

---

## 機器 A 端推薦呼叫流程

```
[Pre-bridge — 維持現有行為]
caller STT FINAL → POST /input {"text": "..."}
  → 拿 outputs（AI 回覆）→ TTS 給 caller
  → 收到 done=true + error 含「轉接專人」 = 觸發 bridge

[Bridge 動作]
撥通真人受理員分機 + 建 conference

[Post-bridge — 切換到 /observe]
caller STT FINAL → POST /observe {"text": "...", "role": "caller"}
  → 拿 updated_fields（可選 log）
agent STT FINAL → POST /observe {"text": "...", "role": "agent"}
  → 拿 updated_fields = []（agent 通常無欄位更新）

[通話結束]
POST /hangup（保留以維持冪等，session 已 done 也安全）
GET /result → 拿包含 bridge 期間所有更新的完整 case JSON
```

> **`/input` 跟 `/observe` 不要混著用**：done=true 後 `/input` 會回 409；done=false 時 `/observe` 會回 409。bridge 動作做完後請統一切到 `/observe`。

---

## 你們三個確認問題的答覆（呼應 transfer-nlp-issue-for-machineB.md）

| Q | 答 |
|---|---|
| Bridge 後 `role=agent` 文字，NLP 是否仍會更新 case_summary？ | agent 自身**不會**觸發抽取（agent 通常是釐清問句、無新資訊），但 caller 後續回答**會**觸發。整段對話都會被 transcript 完整記錄。如果你們希望 agent 也觸發抽取，告訴我們，加個 query param 即可 |
| Bridge 後 `outputs` 有回覆文字，機器 A 忽略不播會影響機器 B 嗎？ | 不影響。但 bridge 後請改用 `/observe`，那個 endpoint 不會回 outputs（沒有 AI 生成回覆），只回 updated_fields |
| `GET /result` 是否會包含 bridge 後新補充的資訊？ | **會**。`/observe` 直接更新 sess.result，後續 `/result` 拿到的就是含 bridge 更新的最新狀態 |

---

## 快速測試（在機器 A 端）

```bash
# 假設 session 已透過 /input 走完 SopEngine + done=true
SID=...

# bridge 後，agent 問句
curl -s -X POST http://192.168.5.204:8100/session/$SID/observe \
  -H "Content-Type: application/json" \
  -d '{"text":"請問您爸爸大概幾歲？","role":"agent"}'
# 預期: {"updated_fields": [], "transcript_size": N+1}

# caller 回答
curl -s -X POST http://192.168.5.204:8100/session/$SID/observe \
  -H "Content-Type: application/json" \
  -d '{"text":"75 歲，身上有手機","role":"caller"}'
# 預期: {"updated_fields": [可能含 caller_phone, caller_name_hint 等], "transcript_size": N+2}

# 通話結束後拿完整 case
curl -s http://192.168.5.204:8100/session/$SID/result | python -m json.tool --no-ensure-ascii
# 預期: case.transcript 含所有 assistant/caller/agent turns
```

---

## 對應的歷史文件

- `talk_fromA/transfer-nlp-issue-for-machineB.md` — 你們提的需求（5/12）
- `talk_toA/transfer-nlp-issue-reply-for-machineA.md` — **本文件**

任何問題傳訊給機器 B 端負責人。
