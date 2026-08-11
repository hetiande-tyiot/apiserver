# 多語言 LLM 模型選擇 — 系統設計文件（draft v0.1）

> 狀態：**草稿，待 review**
> 日期：2026-07-02
> 範圍：機器A（apiserver）為主，並定義對機器B / 機器C / amidaemon / 機房的相依與合約。

---

## 1. 背景與目標

現行系統只能處理**國語**報案：STT 用賽微 Cyberon `freeSTT-zh-TW`（國語），LLM/分類走國語流程。當報案人講**其他語言**（台語、客語、英、日、韓、越、印尼…）時，STT 吐亂碼、案件分析失準。

**功能目標**：讓系統能依報案人語言，選用對應的 STT 引擎與 LLM 模型，並支援：

1. **一律先走中文** → 開場不做路由決定，預設國語 / 賽微，不影響接通速度。
2. **背景語言辨識 → 提示前端** → 通話中同時跑語言辨識；偵測到非中文（對應模型與目前不同）即**發提示**給機器C，**不自動切換**。
3. **前端隨時可切** → 受理員決定要轉時，機器C 送指令，機器A 隨時換模型（STT / LLM / TTS）。中文變體（國/台/客 Whisper 分不出）也主要靠這條人工指定。

**非功能需求**：
- **每句回覆延遲 < 2 秒**（含資源池機制帶來的任何額外延遲）。任何做法若讓回覆慢於 2 秒即不可採用。
- **輸出一律繁體中文**：STT 結果、LLM 輸出都需簡→繁轉換（見 §8）。

---

## 2. 現況與限制

| 元件 | 執行資源 | 語言能力 | 容量限制 |
|------|---------|---------|---------|
| 賽微 Cyberon **STT** | **CPU** | 國語 / 英文 / 台語 | **32 路**（授權） |
| 賽微 Cyberon **TTS** | **CPU** | 國 / 英 / 台（語音） | **32 路**（授權，與 STT 獨立） |
| Whisper **STT + 語言偵測** | **GPU** | 其他語言（含中文變體，但**分不出國/台/客**；**可能輸出簡體**） | 依 GPU |
| **LLM / 110LLM** | **GPU** | 依模型（**可能輸出簡體**） | 依 GPU |
| F5-TTS（[config.py](../../config.py) port 8089） | **GPU** | 多語語音 | 依 GPU |

**未來機房**：2 台 server（CPU 控制平面）+ 10 台 aitop100（各 1×RTX 5090）。尖峰 **~60 通**同時在線。

**關鍵限制**：
- 賽微 STT / TTS 各 **32 路**是硬上限，60 通會超賣 → 需「每句釋放 + 溢出 GPU」，但**受 <2 秒延遲約束**（見 §5）。
- Whisper 用 ISO 語言碼，**國/台/客都被判成 `zh`** → 中文變體無法自動區分，需人工指定。
- Whisper / LLM **可能輸出簡體字** → 需統一轉繁（見 §8）。
- 現行 [state.py](../../state.py) 是 **in-process dict**，無法跨多實例 A / 跨 amidaemon 共用 → 上叢集前需 Redis 化。

---

## 3. 整體架構：控制平面 + GPU 池

機器A 從「單機中控」升級為**控制平面 / 路由器**：握有每通電話的 session→語言/模型對應，把 STT / LLM / TTS 分別導到對的資源。

```
                     ┌─────────── 控制平面（2 台 server, CPU）───────────┐
  電話 ── amidaemon ─┤  機器A(FastAPI 路由)  Redis(session+線池)  MySQL   │
                     └──┬──────────┬──────────────┬──────────────┬───────┘
       語言=國/英/台+池未滿│ 語言=其他/池滿│   每輪 LLM     │   每句 TTS
                     ┌──▼──────┐ ┌─▼────────┐ ┌──▼────────┐ ┌──▼────────────┐
                     │Cyberon  │ │Whisper 池│ │  LLM 池   │ │Cyberon TTS(32)│
                     │STT(32路)│ │ (GPU×3)  │ │ (GPU×4)   │ │  或 F5(GPU×2) │
                     └─────────┘ └──────────┘ └───────────┘ └───────────────┘
```

**設計原則**：
1. **控制平面有狀態**（session、線池計數）；**GPU 做成無狀態池**，可獨立擴縮，不把一通電話釘死在某台。
2. **A 主導路由**：A 依 session 語言選 STT/LLM/TTS 目標；B/模型服務只單純執行。
3. **賽微 32 路當可預約資源池**：Redis semaphore，STT/TTS 各一個，滿了溢出 GPU。

---

## 4. 語言與引擎分工

| 語言 | STT | LLM | TTS |
|------|-----|-----|-----|
| 國語 | Cyberon（池未滿）/ Whisper（溢出） | 國語模型 | Cyberon / F5（溢出） |
| 英文 | Cyberon / Whisper（溢出） | 英文模型 | Cyberon / F5 |
| 台語 | Cyberon / Whisper（溢出） | 台語模型 | Cyberon / F5 |
| 其他（客/日/韓/越/印…） | **Whisper（必走 GPU）** | 對應模型 | **F5（必走 GPU，賽微不會念）** |

對外一律用**語言代號**（`mandarin`/`english`/`taiwanese`/`hakka`/…），機器A/C 不碰底層實體模型名；由 B/模型服務內部對應。清單由 B 的 `GET /models` 提供。

---

## 5. 資源池與容量規劃（32/32）＋ 延遲約束

賽微 STT、TTS 為**兩個獨立 32 路池**。

| 池 | 供給 | 負載估算（60 通） | 結論 |
|----|------|------------------|------|
| STT | 32 路 | 每句釋放、duty ~35% → **~21 erlang** | 夠扛 ~55–60 通，**前提：每句釋放且不破 <2 秒**（整通持有上限 32 通）|
| TTS | 32 路 | 每句合成、duty ~25% → **~15 erlang** | **輕鬆扛 60 通**，幾乎零阻塞 |

### 5.1 「每句釋放」必須不破 <2 秒
**不可用「FINAL 後才重連」**的做法 —— 那會把 handshake（~100–300ms）加進關鍵路徑，拖慢回覆。

**正解：VAD 語音起點就預開連線 + pre-roll buffer**
- VAD 一偵測到人聲起點（在報案人**還沒講完前**）就開賽微連線；handshake 與講話時長重疊、被吃掉，**FINAL 延遲不受影響**。
- 保留 ~300ms pre-roll buffer，連上後補送，避免首字被切掉。
- 線只在「講話期間 + 短尾」佔用（duty ~35%），靜音時釋放。

### 5.2 Fallback（若工程上仍無法穩定 <2 秒）
- 賽微改回**整通連續連線**（延遲最低）→ Cyberon 上限 **32 通**，第 33+ 通**溢出 Whisper GPU**（Whisper streaming 也須驗證 <2 秒）。
- 代價：Whisper 併發需求上升（~28 併發 → 5–7 張 GPU），擠壓 GPU 預算 → 此情境**傾向加購賽微線數**（32→64）。
- → **延遲 ↔ 線數 ↔ GPU ↔ 授權成本互相牽動，壓測後定案。**

### 5.3 其他做法
- **STT / TTS 各一個 Redis semaphore**（STT 在 amidaemon、TTS 在機器A，需共享計數）。
- **池滿溢出 GPU**：STT→Whisper、TTS→F5-TTS。其他語言本來就必走 GPU。

---

## 6. GPU 叢集配置（10×5090，尖峰 60 通）

洞察：**只有 STT 是全程/講話期間佔用；LLM/TTS 是每輪突發**，GPU 並發需求遠低於 60。

| 池 | 建議張數 | 理由 |
|----|---------|------|
| **LLM 推論** | **4–5** | vLLM continuous batching，7–14B 級吃 60 通突發輪次（實際並發 ~10–20）綽綽有餘；多語言在池內掛多模型/replica |
| **Whisper STT** | **3** | 其他語言 + 賽微溢出 + HA（若走 §5.2 fallback 需上修至 5–7） |
| **F5-TTS** | **2** | 其他語言語音 + TTS 溢出 |

**2 台 server（CPU 控制平面）**：
- Server 1：機器A（FastAPI）+ amidaemon（AMI）+ 賽微 STT/TTS（CPU）+ Redis + 負載平衡
- Server 2：MySQL（police110）+ 機器C 整合 + 監控/log + 機器A/賽微 HA 備援

⚠️ **待壓測**：賽微 STT/TTS CPU 版在 32 路連續負載下的 CPU 用量；核心數不足時把部分國/英/台 STT 也分流 Whisper。

---

## 7. 模型選擇機制（一律先中文 → 背景辨識 → 提示前端 → 前端隨時切）

### 7.1 開場：一律先走中文
- **開場不做路由決定**，預設國語（Cyberon STT + 國語 LLM）。
- 好處：省掉開場 LID 等待、不影響接通速度；絕大多數報案是國語，預設命中率高。

### 7.2 背景語言辨識 → 提示前端（不自動切）
- amidaemon 同時把音訊餵給 Whisper 背景 LID（`detect_language`），與賽微 STT **並行、不在關鍵路徑**。
- LID 結果帶進 `POST /call/stt` 的 `language` 欄（或另開一支通報）。
- 機器A：當 `language` 對應的模型 ≠ 目前模型（即偵測到非中文/他語）→ **發提示** `POST /api/AiRobot/LanguageSuggest` 給機器C，**不自動切模型**。
- **去重**：每通、每個建議語言只提示一次，避免洗版（比照現有 TransferSuggest）。

### 7.3 前端隨時切換（實際換模型在這裡發生）
- 受理員看到提示、或自行判斷 → 機器C 送 `POST /api/AiRobot/SwitchModel {callId, model}`。
- 機器A 轉發 B `POST /session/{id}/model` 切 LLM，並切換該通的 STT / TTS 引擎路由。
- **隨時可切**，不限於收到提示後；中文變體（國/台/客）也主要靠這條人工指定。

---

## 8. 繁簡轉換（簡→繁）

**問題**：Whisper、部分 LLM 可能輸出**簡體字**；台灣一律用**繁體**（且慣用台灣用語）。

**需求**：以下兩處文字都要過簡→繁：
1. **STT 結果**：`POST /call/stt` 收到 `text` 後，**先轉繁**，再存 MySQL / 推 MC Conversation / 餵 LLM。
2. **LLM 輸出**：`sop.push` 的 `outputs`、以及 `case` 各欄位值（caseSummary、caseDetails…），**轉繁後**再 TTS / 推 MC / 存 case。

**工具**：OpenCC，設定 **`s2twp`**（簡體→繁體 + 台灣用語轉換）；純字形可用 `s2t`。CPU 極輕、可忽略延遲。

**實作位置**：集中在機器A [utils.py](../../utils.py) 加 `to_traditional(text)`，於上述兩處呼叫。轉換為 **idempotent**（繁轉繁不變），賽微本來就輸出繁體也不受影響，統一過一次最保險。

**注意**：`case` 是巢狀 dict，需遞迴/逐值轉；`transcript` 等長欄位可視情況略過（已另存原文）。

---

## 9. 機器A 程式碼改動（第 1 層，與叢集無關、先做）

| 檔案 | 改動 |
|------|------|
| [config.py](../../config.py) | `DEFAULT_LLM_MODEL`、`WHISPER_LANG_TO_MODEL` 對照、`OPENCC_CONFIG`（預設 `s2twp`） |
| [utils.py](../../utils.py) | 新增 `to_traditional(text)`（OpenCC） |
| [sop_client.py](../../sop_client.py) | `new_session(model=…)`、新增 `switch_model()`、`list_models()` |
| [payloads.py](../../payloads.py) | `CallStartPayload` 加 `model`；`SttPayload` 加 `language`；新增 `SwitchModelPayload` |
| [state.py](../../state.py) | `MCSessionState` 加 `llm_model`；**（叢集階段）搬 Redis** |
| [machinec_client.py](../../machinec_client.py) | 新增 `language_suggest()` 推 C（提示，非自動切） |
| [api_server.py](../../api_server.py) | `/call/stt` 入口轉繁 + 依 `language` **發提示（去重）**；LLM 輸出轉繁；`/call/start` 帶 model；新增 `POST /api/AiRobot/SwitchModel`、`GET /api/AiRobot/Models` |
| [tts_client.py](../../tts_client.py) | （第 2/3 層）合成前 acquire 賽微 TTS 線池，滿則走 F5，完成 release |

---

## 10. 對機器B 的介面合約（需 B 新增）

1. `GET /models` → `{"models":[{"id":"mandarin","label":"國語","default":true}, ...]}`
2. `POST /session/new` body 新增選填 `{"model":"..."}`（不帶 = 預設，**向後相容**）
3. `POST /session/{id}/model` body `{"model":"..."}` → `{"success":true,"model":"...","previous":"..."}`；錯誤 404（無 session）/409（已結束）/400（未知模型）

（另出一份 `docs/talk_toB/` 正式規格。）

---

## 11. 對機器C 的介面合約（A 對外，`/api/AiRobot/*`）

| API | 方向 | 說明 |
|-----|------|------|
| `GET /api/AiRobot/Models` | C→A | 取可選語言/模型清單（前端下拉）|
| `POST /api/AiRobot/LanguageSuggest` | A→C | 背景辨識到非中文時**提示**前端（不自動切，比照 TransferSuggest 去重）|
| `POST /api/AiRobot/SwitchModel` | C→A | 受理員決定切換，隨時可送；A 轉發 B 換模型 |

（另出一份 `docs/talk_toC/` 正式規格。）

---

## 12. 狀態 Redis 化（上叢集前必做）

現行 in-process：`sop_sessions`、`turn_counters`、`mc_state`。多實例 A + 跨 amidaemon 共用時需搬 Redis：
- session→模型/sop_id 映射
- 賽微 STT / TTS 兩個 semaphore（線池計數）
- MC 推送去重旗標

---

## 13. 待決問題

1. **LID 放哪**：開場語言偵測跑在 amidaemon 呼叫 Whisper 池，還是 A 收第一句文字後判斷？（傾向前者，有聲音才準）
2. **每句釋放 vs <2 秒**：VAD 預開連線能否穩定守住 <2 秒？壓測後決定走 §5.1 還是 §5.2 fallback。
3. **賽微加購線數** vs GPU 溢出的成本比較。
4. **LLM 是否 per-language 各一模型**，還是單一多語模型？影響 LLM 池記憶體與路由。
5. **中文變體判斷**：國/台/客除了人工指定，是否加一個中文變體分類器輔助？
6. **繁簡轉換範圍**：`transcript` 等長欄位要不要一起轉？巢狀 case dict 的轉換效能。
7. **語言提示重發策略**：受理員忽略提示後，若後續 LID 更確定或改變，是否重發？（比照 TransferSuggest，目前傾向不重發）

---

## 14. 分階段實作建議

| 階段 | 內容 | 相依 |
|------|------|------|
| **P1** | 機器A model-selection 層 + 繁簡轉換（§9 前 7 項）+ B/C 合約文件 | B 需先做 §10 端點 |
| **P2** | 上游 Whisper 背景 LID + Cyberon/Whisper 引擎分流（amidaemon） | GPU 機到位 |
| **P3** | 賽微 32 路池化（VAD 預開/每句釋放）+ Redis semaphore + TTS F5 溢出 + <2 秒壓測 | Redis、F5 |
| **P4** | state 全面 Redis 化 + 機器A 多實例 + 負載平衡 | 叢集機房 |

**建議先啟動 P1**：不依賴叢集、可獨立測，且能同步推動 B 端做 §10 端點。繁簡轉換更是**現在就能單獨做、立即受益**的一項。
</content>
