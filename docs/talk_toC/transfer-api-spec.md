# 轉接受理員 API 規格

> 日期：2026-05-15（2026-05-17 修訂：取消 AI 自動轉接，統一改由受理員介入觸發）
> 撰寫：AI 受理系統側

---

## 背景說明

目前 AI 受理系統已能完成：
- 民眾來電 → AI 接聽（語音辨識 + NLP）→ 對話互動
- 即時將對話內容（Conversation）與案件分析（Analysis）推送給前端系統

**轉接機制設計（已定案）：**

由受理員透過前端介面，按下「介入當前通話」按鈕來執行轉接。AI 系統**不再主動觸發轉接**，僅在 NLP 判定建議轉接時送一個「建議提示」給前端，由受理員自行決定是否介入。

| 情境 | 行為 |
|------|------|
| **NLP 建議轉接**（例如案件超出 AI 範圍） | AI 持續正常對話；同時送 `/TransferSuggest` 給前端，前端跳提示視窗 |
| **受理員按介入** | 前端送 `/Transfer` 給 AI；AI 立即打斷 TTS、撥通該受理員分機 |
| **受理員不介入** | AI 繼續對話，使用者持續接受 AI 服務 |

本文件定義雙向共 5 支 API 的規格。

---

## 名詞定義

| 名詞 | 說明 |
|------|------|
| `callId` | 通話唯一識別碼（UUID 格式），由 AI 系統產生，貫穿整通電話 |
| `agentExtension` | 受理員的 SIP 分機號碼（如 `"1008"`），AI 系統會用此號碼透過電話交換機 `Dial(PJSIP/1008)` 撥通真人 |
| `hasCapacity` | 當前是否有任何受理員待機中（純佈林值，不指定具體分機） |

---

## 端點總表

| API | 方向 | 前端狀態 | AI 側狀態 |
|-----|------|---------|----------|
| ① `POST /api/AiRobot/CallStart` | AI → 前端 | 規格保留 | **暫不實作** |
| ② `POST /api/AiRobot/TransferSuggest` | AI → 前端 | 待實作 | 已實作 |
| ③ `POST /api/AiRobot/Transfer` | 前端 → AI | 待實作 | 已實作 |
| `POST /api/AiRobot/Conversation` | AI → 前端 | 已實作 | 已實作 |
| `POST /api/AiRobot/Analysis` | AI → 前端 | 已實作 | 已實作 |

---

## API ①：通話開始通知

**方向：** AI 系統 → 前端系統
**時機：** 民眾撥入、AI 開始接聽時立即呼叫
**目前狀態：** **規格保留，AI 側暫不實作**（見下方說明）

### Request

```
POST /api/AiRobot/CallStart
Content-Type: application/json

{
    "callId":       "4975a464-c14c-46be-8c60-32397df00d15",
    "channelName":  "PJSIP/1003-0000007f",
    "calledNumber": "4000",
    "startTime":    "2026-05-15T14:00:00",
    "callType":     "AI"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `callId` | string | 通話唯一識別碼 |
| `channelName` | string | Asterisk channel 名稱（除錯用） |
| `calledNumber` | string | 民眾撥打的號碼（如 `"4000"` 表示 AI 受理） |
| `startTime` | string (ISO 8601) | 通話建立時間 |
| `callType` | string | 預留欄位，目前固定為 `"AI"`（未來可能有 `"Direct"` 等其他類型） |

> **註：本 payload 不含 `callerPhone`。**
> 真實民眾來電號碼來自前端系統上游的電信設備，AI 系統位於更內層、無法取得此資訊。
> 受理員介面顯示的來電號碼應由前端系統從電信側自行對應，AI 側僅提供 `callId` 等內部識別資訊。

### Response

```json
{
    "success":     true,
    "hasCapacity": true
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `success` | boolean | 處理是否成功 |
| `hasCapacity` | boolean | 當前分機群是否有人待機（資訊性質） |

> **註：** AI 系統暫不依 `hasCapacity` 值改變流程，建議如實回傳即可。

### 為何 AI 側暫不實作

- 前端系統能從第一筆 `/Conversation`（AI 開場白）推斷新通話開始，已足夠 UI 顯示
- AI 側無法提供 `callerPhone` 等關鍵資訊，CallStart 帶來的額外資訊有限
- 待未來確有需求（例如前端系統需要更早收到通知、或要傳 `callType` 之類資訊）再實作

規格保留供前端系統參考；若日後啟用，AI 側只需於 `STT_CALL_START` 時機加入此呼叫即可。

---

## API ②：NLP 建議轉接提示

**方向：** AI 系統 → 前端系統
**時機：** NLP 首次判定建議轉接（例如案件超出 AI 受理範圍、或對話流程已完成需真人接手）

> **重要：** AI 系統送出此通知後**不會自動轉接**，僅提示前端讓受理員決定。AI 持續正常對話與 NLP 分析，直到受理員按介入或電話掛斷。
> 每通電話此 API **只會送一次**（去重），避免重複跳出提示視窗。

### Request

```
POST /api/AiRobot/TransferSuggest
Content-Type: application/json

{
    "callId":       "4975a464-c14c-46be-8c60-32397df00d15",
    "reason":       "out_of_scope",
    "caseTypeName": "打架",
    "caseAddr":     "新北市新莊區中正路 100 號",
    "caseSummary":  "民眾報案稱中正路 100 號前有兩名男子發生肢體衝突",
    "callerPhone":  "0912345678",
    "timestamp":    "2026-05-15T14:01:30"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `callId` | string | 通話 UUID |
| `reason` | string | 建議轉接的原因，列舉值見下表 |
| `caseTypeName` | string | NLP 推斷的案件類別（可能為空） |
| `caseAddr` | string | NLP 推斷的事發地點（可能為空） |
| `caseSummary` | string | NLP 整理的案情摘要（可能為空） |
| `callerPhone` | string | 民眾電話 |
| `timestamp` | string (ISO 8601) | 通知時間 |

**`reason` 列舉值：**

| 值 | 說明 |
|----|------|
| `out_of_scope` | NLP 判定案件超出 AI 受理範圍 |
| `user_request` | 民眾明確要求轉接真人 |
| `ai_complete` | AI 對話流程已完成，案件需交給真人後續處理 |

### Response

```json
{ "success": true }
```

失敗：

```json
{ "success": false, "error": "錯誤訊息" }
```

### 前端建議行為

- 收到此 API 時，跳出提示視窗給當前正在監看該通話的受理員
- 視窗顯示案件摘要與建議原因
- 受理員可選擇「介入」或「忽略」
- 若選擇介入 → 前端送 `/Transfer` 給 AI（API ③）
- 若忽略 → AI 持續對話，直到通話結束

---

## API ③：受理員介入指令

**方向：** 前端系統 → AI 系統
**時機：**
- **介入：** 受理員按下「介入當前通話」按鈕
- **重新派工：** 前一支分機未在合理時間內接通，前端決定改派他人

### Request

```
POST /api/AiRobot/Transfer
Content-Type: application/json

{
    "callId":         "4975a464-c14c-46be-8c60-32397df00d15",
    "agentExtension": "1008",
    "reason":         "intervene",
    "timestamp":      "2026-05-15T14:02:00"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `callId` | string | 通話 UUID |
| `agentExtension` | string | 要接手的受理員分機 |
| `reason` | string | `"intervene"`（介入）或 `"reassign"`（重新派工） |
| `timestamp` | string (ISO 8601) | 指令時間 |

### Response

```json
{ "success": true }
```

失敗：

```json
{ "success": false, "error": "錯誤訊息" }
```

### AI 系統收到後行為

| 情境 | 行為 |
|------|------|
| AI 正在播 TTS 對話 | **立即打斷** TTS、停止 AI TTS 輸出 → Dial 指定分機 |
| AI 正在等民眾說話 | 停止 NLP `/input` 處理 → Dial 指定分機 |
| 已在 Dial 其他分機（`reassign`） | 中止當前 Dial → 改 Dial 新分機 |
| 已經接通真人 | 視同 reassign，重新派工 |

接通真人後：caller 與 agent 雙通道 STT 都會送進 NLP `/observe`，持續更新 case summary，AI 不再產生 TTS。

---

## 「分機未接通」處理策略

當 AI 系統 `Dial(PJSIP/分機, 60秒, ...)` 後 60 秒未接通，AI 系統**不會自動換分機**，等待前端決定。

**建議由前端側完整掌控**，理由：
- 受理員的登入狀態、忙線狀態、是否可接通 → 前端系統最清楚
- AI 系統不持有受理員的可用性資訊，自行判斷容易誤判

**建議前端流程：**

```
前端 POST /Transfer (agent=1008)
  → 前端開始計時或監控該分機狀態
  → 若 N 秒未接 / 該員按拒接
  → 前端 POST /Transfer (agent=1009, reason="reassign")
```

AI 系統會 cancel 當前 Dial 並改撥新分機。

---

## 完整流程圖（情境一：受理員主動介入）

```
民眾撥 4000
  │
  ▼
AI 系統 ──POST CallStart──▶ 前端
                        ◀──{ hasCapacity: true }──

AI 與民眾對話（前端即時看到 Conversation / Analysis）...

受理員按下「介入」
  │
  ▼
前端 ──POST Transfer (agent="1006", reason="intervene")──▶ AI 系統
                                                        ◀──{ success: true }──

  ▼
AI 立即停止 TTS → Dial(PJSIP/1006)
  ▼ 接通
雙方通話開始（持續推 Conversation/Analysis 給前端）
```

## 完整流程圖（情境二：NLP 建議轉接）

```
民眾撥 4000
  │
  ▼
AI 系統 ──POST CallStart──▶ 前端

AI 與民眾對話 ... NLP 判定建議轉接（例如案件超出 AI 範圍）
  │
  ▼
AI 系統 ──POST TransferSuggest──▶ 前端
                              ◀──{ success: true }──

前端：跳提示視窗給受理員（顯示案件摘要）
AI：繼續正常 AI 對話（不停 TTS、不停 NLP）

  ┌─── 情境 A：受理員按介入 ──┐    ┌─── 情境 B：受理員忽略 ──┐
  ▼                          ▼    ▼                      ▼
前端 POST Transfer            AI 持續對話直到掛斷
AI 中斷 TTS → Dial 分機       MC 不會再收到 Suggest
```

---

## 待確認 / 待補充事項

1. **API URL Base 路徑** — AI 系統 API 端點為 `http://<我方IP>:8200`（實際 IP 待雙方確認網路連通方式後告知）；前端 endpoint 也請告知。
2. **`callerName`** — `CallStart` 是否需要帶民眾姓名？若有資料庫對照可加。
3. **`/TransferSuggest` 重發** — 目前設計每通電話只送一次。若受理員忽略後 NLP 再次建議（例如使用者後續發言改變了判斷），是否要再送？目前傾向不重送。
4. **錯誤處理** — 任一 API 失敗時的重試策略需另外討論。

---

## 修訂歷史

- **2026-05-15** 初版：規劃兩種轉接情境（受理員介入 + AI 自動派工）
- **2026-05-17** 修訂：取消 AI 自動派工（`/AssignAgent` 移除），統一改由受理員介入觸發。新增 `/TransferSuggest` 讓 NLP 建議轉接時提示前端。
