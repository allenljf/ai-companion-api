# AI Companion API — 獨立雲端服務

## 這個專案是什麼

把原本在 kkday-b2c-api（PHP / Laravel）的「AI 旅伴」功能，重寫成獨立的 Python / FastAPI 雲端服務，部署在免費方案上、串接免費 LLM 與免費圖片生成。

**範圍**：Phase 1 + Phase 2 共 17 支 API（含產圖，但只做 share-image-v2、不做 legacy v1）。

## 開始工作前必讀

| 文件 | 內容 | 什麼時候讀 |
|---|---|---|
| **`docs/source-spec.md`** | 原始服務的完整規格：17 支 API 的輸入輸出、**全部 prompt 全文**、normalize 規則、每個設計決策的理由 | **每次開工都要讀對應章節**——這是唯一的規格真相 |
| **`docs/migration-plan.md`** | 9 個階段的路線圖、驗收條件、prompt 調校方法、風險清單、**進度表** | 每次開工先看進度表確認做到哪 |
| `reference/` | 原始 PHP 實作的**唯讀快照**（見下） | 需要確認實作細節時查 |

## reference/ 是唯讀參考，不是要移植的程式碼

`reference/` 放的是原始 PHP 專案的快照，**只用來查證，永遠不要修改它，也不要直接翻譯它的結構**。

| 資料夾 | 內容 | 用途 |
|---|---|---|
| `reference/services/` | 14 個 PHP Service | **prompt 原文的最終真相**（`docs/source-spec.md` 第 8 章有摘錄，但這裡是完整版）；業務邏輯的行為參考 |
| `reference/traits/` | normalize / prompt 組裝的共用邏輯 | **最重要**：`ItineraryDayNormalizeTrait.php` 是整個系統最核心的一層，移植時逐條對照 |
| `reference/requests/` | 12 個 FormRequest | **驗證規則 = Pydantic schema 的來源**（必填、長度上限、列舉值都在這） |
| `reference/prompt-fixtures/` | 產圖用的 art_style / hero / stamp / tag prompt | 階段 8 產圖時使用 |
| `reference/fake-data/` | orders / wish_list / history 假資料 | 直接搬進新專案的 `data/fake/` |
| `reference/openapi/` | 12 份 OpenAPI spec | 欄位級細節（nullable、maxLength）的交叉確認 |

> **不要照抄 PHP 的類別結構**。PHP 用 Trait 是因為它沒有好的組合機制；Python 應該用函式模組 + Pydantic。要移植的是**行為與 prompt**，不是程式碼形狀。

## 技術決策（已定案，不要重新討論）

| 項目 | 決定 | 理由 |
|---|---|---|
| 語言 | **Python 3.11+ / FastAPI** | 上手快、Pydantic 處理 LLM 輸出正規化最順 |
| HTTP client | **httpx (async)** | LLM 呼叫是 I/O bound；**timeout 要設 ≥90s**（travel-guide 要 30s） |
| LLM | 免費方案，**provider 抽象 + 每支 API 可獨立指定模型** | 換 provider 是高機率事件 |
| 產圖 | **只做 share-image-v2，放棄 legacy v1** | 免費圖片模型不會渲染中文；v2 本來就不需要模型寫字 |
| 儲存 | SQLite（cache/gallery/lock）+ 物件儲存（圖片） | 免費單機部署下夠用，少一個外部依賴 |
| prompt | **抽成 `app/prompts/*.txt` 檔案**，不內嵌程式碼 | 好 diff、好調校、可 A/B |
| prompt 模板語法 | **Jinja2**（不要用 `str.format()`） | prompt 內含大量 `{` `}` JSON 範例，會與 format 衝突 |

## 三個絕對不能弄錯的設計原則

移植時最容易搞砸的三件事，`docs/source-spec.md` 第 3 章有完整說明：

### 1. 軟失敗：LLM 失敗 ≠ API 失敗

所有走 LLM 的端點，**失敗一律回 HTTP 200 + `fail_reason` 有值 + 可渲染的兜底內容**，絕不回 5xx。
只有 request body 驗證失敗才回 400。

### 2. Pydantic 要「強制轉型或給預設」，不能 raise

**這是 Python 版最容易踩的坑。** 原始的 normalize 哲學是「LLM 亂給就轉安全預設、絕不拋錯」，但 Pydantic 預設驗證失敗就 raise。

```python
# ❌ 錯：LLM 回 "hotel" 就 500
type: Literal["spot", "logistics", "meal"]

# ✅ 對：用 mode="before" 的 validator 強制轉型
@field_validator("type", mode="before")
def _coerce(cls, v): return v if v in ("spot","logistics","meal") else "spot"
```

另外：`lat`/`lng`/`transport_mode` 在不適用的 item 上是**移除整個 key**，不是設 None。

### 3. 識別碼絕不進 prompt

需要「輸入項目 ↔ 判斷結果」對映時，**只給位置索引**，`prod_id`/`oid` 由後端從原始請求取回。
模型從沒見過 id，就不可能捏造。詳見 source-spec 技法 2。

## 工作流程（SDD）

規格已經寫好了（`docs/source-spec.md`），所以每個 session 是「照規格實作一個階段」而不是重新設計。

```
1. 讀 docs/migration-plan.md 的進度表 → 確認這次要做哪個階段
2. 讀 docs/source-spec.md 對應章節 → 複述這階段的驗收條件，確認理解一致
3. 實作：
   - 純函式（normalize / mapper / prompt 組裝）→ 先寫測試（TDD）
   - I/O（LLM / 儲存）→ 先 mock 測試，再實打
4. 收尾（缺一不可）：
   - 部署上去
   - 用真實 LLM 打過
   - 逐項對照驗收條件
   - 更新 migration-plan.md 的進度表（含「實際踩到的問題」欄）
```

**切片原則：垂直切**——一支 API 端到端（schema + service + route + test + 部署 + 實打），不要「所有 model → 所有 service → 所有 route」。每個 session 結束都要有能跑的東西。

## 已知的資料缺口

| 缺什麼 | 影響階段 | 怎麼補 |
|---|---|---|
| ~~DCS `ai_quiz.dimensions`（測驗題庫）~~ | ~~階段 7~~ | ✅ **已補**（2026-08-15，存於 `data/quiz_dimensions.json`：8 維度 / 149 選項；選項圖已壓縮上傳自有 GCS `.../quiz/*.jpg`；原始 DCS 誤塞在維度 8 內的產圖 prompt 已抽出到 `app/prompts/phase1/image_*.txt`，非 prompt 設定放檔內 `image_config`） |
| ~~DCS `ai_partner`（人格/風格選項）~~ | ~~階段 1 起~~ | ✅ **已補**（2026-08-14，version 11 存於 `data/ai_partner.json`；內含 `partner_intro_prompt`，可直接供階段 7 self-introduction 使用） |
| ~~DCS 版的 `response_prompt`~~ | ~~階段 7~~ | ✅ **已補**（2026-08-15，存於 `app/prompts/phase1/quiz_completion.txt`，含 `reasoning` 逐句與分段 `recommendation` 要求，優於 source-spec 8.3 的 fallback 版） |

## 指令

```bash
# 開發（待階段 0 建立後可用）
uvicorn app.main:app --reload

# 測試
pytest                              # 一般測試（mock LLM）
pytest -m prompt_regression         # 真實 LLM 的 prompt 回歸測試（會消耗額度，手動跑）
```

## 語言偏好

用繁體中文回應說明、解釋與溝通；程式碼、指令、檔案路徑維持原始格式。
