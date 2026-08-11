# 轉接真人後雙通道 NLP 需求（機器A → 機器B）

> 日期：2026-05-12

---

## 背景說明

機器A 已完成以下功能：

- 民眾來電 → AI（110LLM）受理 → STT 即時辨識 → NLP 產出回覆與案件摘要
- 當 110LLM 判斷需轉接真人（`error: "轉接專人"`），自動撥通受理員分機並 bridge 通話
- Bridge 後雙方通話均有獨立 STT（caller / agent 雙通道）

目前問題：**Bridge 後，受理員說的話無法送進 NLP，case_summary 只有民眾說話的內容。**

---

## 需求一：`/session/{id}/input` 支援 `role` 欄位

### 現況

目前機器A 呼叫方式：

```json
POST /session/{session_id}/input
{
  "text": "我爸爸在府中路走失了"
}
```

NLP 視所有輸入為 `caller`（民眾）說的話。

### 需求

Bridge 後，受理員說的話也要送進同一個 NLP session，請確認機器B 是否可以支援：

```json
POST /session/{session_id}/input
{
  "text": "請問您爸爸大概幾歲？身上有帶手機嗎？",
  "role": "agent"
}
```

### 目的

- NLP 能區分「民眾說的」和「受理員問的」
- `case_summary` 可以根據受理員釐清的資訊持續更新
- 最終 case JSON 包含完整雙方對話內容

### 機器A 目前行為（待接上）

- Caller STT FINAL → `POST /session/{id}/input`（無 role 欄位，現有行為）
- Agent STT FINAL → 暫存 MySQL `utterances`（`role='agent'`），**尚未送 NLP**，等機器B 確認支援後才接上

---

## 需求二：確認 `case_summary` 即時更新邏輯

### 問題

目前機器A 收到每句 STT 後都呼叫 `/session/{id}/input`，機器B 每次會回傳 `outputs`（AI 回覆文字）。

Bridge 後，機器A **不需要** AI 回覆民眾（真人受理員在講話），但**仍需要** NLP 繼續更新 case_summary（地點、案類、人員特徵等欄位持續補齊）。

### 請機器B 確認

1. Bridge 後送進去的 `role=agent` 文字，NLP 是否仍會更新 `case_summary`？
2. Bridge 後如果 `outputs` 有回覆文字，機器A 會忽略不播放（不影響通話），機器B 這邊是否有影響？
3. 通話結束後 `GET /session/{id}/result` 回傳的 case JSON，是否會包含 Bridge 後新補充的資訊？

---

## 需求三：通話結束後完整 transcript

### 現況

目前 case JSON 的 `transcript` 只有 `role: caller` 和 `role: assistant` 的對話。

### 需求

希望最終 transcript 也能包含 `role: agent`（受理員說的話），讓 case JSON 完整紀錄整通電話。

如果機器B 的 NLP session 不支援在 transcript 中記錄 agent，機器A 可以在通話結束後，將 MySQL `utterances` 中的 `role=agent` 資料自行補進 case JSON，再存回 MySQL。

**請確認：機器B 這邊 transcript 是否會自動納入 `role=agent` 的輸入，還是需要機器A 自行合併？**

---

## 參考：機器A 目前 STT 資料流

```
[Bridge 前]
民眾說話 → caller STT → POST /session/{id}/input (role=caller)
                       → NLP 回覆 → TTS 播放給民眾

[Bridge 後]
民眾說話 → caller STT → POST /session/{id}/input (role=caller)  ← 繼續送，但不播 TTS 回覆
受理員說話 → agent STT → MySQL utterances (role=agent)           ← 待機器B確認後再送 NLP

[通話結束]
→ GET /session/{id}/result → case JSON → MySQL cases
```

---

## 參考：機器A MySQL 儲存格式

```
utterances 表：
  uuid      - 通話唯一識別
  role      - 'caller' 或 'agent'
  stt_text  - STT 辨識結果
  created_at
```

---

## 對應文件

- `talk_fromB/stt-issue-reply-for-machineA.md` — STT 延遲問題回覆（已解決）
- `talk_toB/transfer-nlp-issue-for-machineB.md` — **本文件**
