# reference/ — 原始 PHP 實作快照（唯讀）

## 這是什麼

從 `kkday-b2c-api`（PHP / Laravel）複製過來的 AI 旅伴實作快照，**取自 2026-08-08**。

**用途：查證細節。不是要被翻譯的程式碼。**

## 三條規則

1. **永遠不要修改這裡的檔案**——它是對照基準，改了就失去意義
2. **不要照抄類別結構**——PHP 用 Trait 是因為它沒有好的組合機制；Python 該用函式模組 + Pydantic。要移植的是**行為與 prompt**，不是程式碼形狀
3. **這是公司程式碼**——已加入 `.gitignore`，不會被 commit 進這個個人專案

## 各資料夾對照表

| 資料夾 | 移植時對應到 | 重點 |
|---|---|---|
| `services/` | `app/services/companion/`、`app/services/plan/` | **prompt 原文的最終真相**。`docs/source-spec.md` 第 8 章有摘錄，但部分章節為了可讀性做了省略（例如 travel-revise 的規則 6~11 寫成「同 travel-guide」），**要完整 prompt 就看這裡的 PHP heredoc** |
| `traits/` | `app/core/normalize.py`、`app/services/*/prompt_sections.py` | ⭐ **最重要**。`ItineraryDayNormalizeTrait.php` 是整個系統最核心的一層（白名單、型別強制、條件性欄位移除、去重），移植時**逐條對照**，`docs/migration-plan.md` 階段 4 有完整測試清單 |
| `requests/` | `app/schemas/` | **Pydantic schema 的來源**。必填、`max:200` 長度上限、`in:` 列舉值、`min:1` 陣列下限全都在這裡 |
| `prompt-fixtures/` | `app/prompts/phase1/image_*.txt` | 階段 8 產圖用。`image_art_style_v2.txt` 是 v2 的水彩風格（v1 的 anime 版不需要）。`hero_style_reference.png` 是 hero 的構圖參考圖，**只在圖片 provider 支援參考圖輸入時才用得到** |
| `fake-data/` | `data/fake/` | 可直接複製使用，格式不用改 |
| `openapi/` | — | 欄位級細節（nullable、maxLength、example）的交叉確認用 |

## 快速索引：哪支 API 看哪個檔案

| API | Service | Request | prompt 在哪 |
|---|---|---|---|
| `quiz` | `CompanionService.php` → `rewriteQuestionsWithLlm()` | `QuizFetchRequest.php` | `buildRewriteSystemPrompt()` |
| `quiz-completions` | `CompanionService.php` → `completeQuiz()` | `QuizCompletionsRequest.php` | `buildCompletionSystemPrompt()`（DCS 有覆寫版） |
| `self-introduction` | `CompanionService.php` → `generateSelfIntroduction()` | `SelfIntroductionRequest.php` | `buildSelfIntroductionSystemPrompt()` |
| `share-image-v2` | `ShareImageV2Service.php` | `ShareImageV2Request.php` | `*_HARD_CONSTRAINTS` 常數 + `prompt-fixtures/` |
| 八人格判定 | `TravelIdentityMapper.php` | — | **不經 LLM**，純規則式 |
| 素材降級分類 | `PosterFallbackCategoryResolver.php` | — | 不經 LLM |
| `travel-summary` | `TravelSummaryService.php` | `TravelSummaryRequest.php` | `buildQuizSystemPrompt()` / `buildOrderPickSystemPrompt()` / `buildImportSystemPrompt()` |
| `travel-summary-from-orders` | `TravelSummaryFromOrdersService.php` | 同名 Request | `buildSystemPrompt()` |
| `travel-summary-from-wish` | `TravelSummaryFromWishService.php` | 同名 Request | `buildSystemPrompt()` |
| `travel-summary-from-history` | `TravelSummaryFromHistoryService.php` | 同名 Request | `buildSystemPrompt()` |
| `recommend-city` | `RecommendCityService.php` | `RecommendCityRequest.php` | `buildSystemPrompt()`（含 banSection / roundInstruction / violationSection 三段條件注入） |
| `travel-guide` | `TravelGuideService.php` | `TravelGuideRequest.php` | `buildSystemPrompt()` + `BookedOrderPromptTrait` + `SelectedProductPromptTrait` |
| `travel-revise` | `TravelReviseService.php` | `TravelReviseRequest.php` | 同上 |

> `LlmProviderInterface.php` / `LlmProxyService.php` / `OpenAiProvider.php` 是 LLM 呼叫層，新專案會用自己的 `app/services/llm/` 取代，這幾個只當介面設計參考。

## 不在這裡的東西

| 缺什麼 | 為什麼 | 怎麼補 |
|---|---|---|
| DCS 題庫（`ai_quiz.dimensions`） | 存在 DCS 遠端設定，不在 repo | 從 DCS 後台匯出，或反覆呼叫 `POST /v3/companion/quiz` 蒐集 |
| DCS 人格選項（`ai_partner`） | 同上 | 直接打 `GET /v3/companion/ai-partner` 取回存檔 |
| DCS 版 `response_prompt` | 同上（線上版比程式碼 fallback 版多了 `reasoning` 與分段 `recommendation`） | 從 DCS 後台取 |
| `AiAgentService` / `OpenAiRequestHelper` | 是 kkday 全站共用的基礎設施，不是 companion 專屬 | 新專案自己寫 `app/services/llm/` |
