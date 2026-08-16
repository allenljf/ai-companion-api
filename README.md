# AI Companion API

把 kkday-b2c-api 的「AI 旅伴」（Phase 1 + Phase 2 共 **17 支 API**）重寫成獨立的 Python / FastAPI 雲端服務，跑在免費層上。

- **規格真相**：`docs/source-spec.md`（17 支 API 的輸入輸出、prompt 全文、normalize 規則）
- **路線圖與各階段實錄**：`docs/migration-plan.md`（進度表的「備註」欄記錄了每個階段實際踩到的坑）
- **原始 PHP 快照**：`reference/`（唯讀查證用）
- **線上服務**：https://ai-companion-api-30568057620.asia-east1.run.app （API 文件：`/docs`）

## 架構與選型（含實測結論）

| 項目 | 選擇 | 實測備註 |
|---|---|---|
| Hosting | **Google Cloud Run**（asia-east1，min-instances 0） | timeout 300s；冷啟動 3.5s、熱請求 0.05s；記憶體 ~60MB/512Mi |
| DB | **Neon Postgres**（免費層） | cache（24h 分析快取）/ 併發鎖 / gallery 三用途；SQLite 因 Cloud Run 暫時檔案系統不可用 |
| LLM | **Vertex AI**（`gemini-3.6-flash`，吃 GCP $300 試用額度） | 輕量任務 `:minimal`、重度結構化 `:low`（thinking 模型要壓思考預算）；ADC 認證免金鑰；Groq/AI Studio key 保留為備援（`LLM_*` env 可切） |
| 產圖 | **Cloudflare Workers AI**（免費層 10,000 neurons/天） | hero 用 `flux-2-klein-4b`（1152×2048 直式，multipart）、裝飾用 `flux-1-schnell`（方圖）；**不支援參考圖**（實測被忽略）→ 旅伴合成砍掉；約 543 neurons/次測驗 ≈ 18 次/天。選型調研：`docs/research/image-gen-free-tier.md` |
| 物件儲存 | **GCS**（公開 bucket `ai-companion-assets-allenljf`） | 頭像/題庫圖/產圖素材 |
| prompt | `app/prompts/*.txt`（Jinja2） | 原 DCS 內容已全部落地成檔案 |

## 端點總覽（17 支 + debug）

| 路由 | 說明 | 限流 |
|---|---|---|
| `GET /v1/companion/ai-partner` | 旅伴設定選項 | — |
| `POST /v1/companion/quiz` | 測驗題目（加權選題 + 語氣改寫） | — |
| `POST /v1/companion/quiz-completions` | 八人格判定 + 文案生成（快取 24h） | — |
| `POST /v1/companion/share-image-v2` | 分享海報素材（hero + 郵戳 + 3 tag） | 10/min |
| `POST /v1/companion/self-introduction` | 旅伴自我介紹 | 10/min |
| `GET /v1/companion/quiz-gallery` | 測驗結果牆（只含產圖成功項） | — |
| `GET /v1/companion/orders` `wish_list` `history` | 假資料端點（前端測試用） | — |
| `POST /v1/plan/travel-summary` | 聊天室初始化摘要（四入口） | 20/min |
| `POST /v1/plan/travel-summary-from-orders` | 帶訂單開場 | 10/min |
| `POST /v1/plan/travel-summary-from-wish` | 從願望清單開場 | 10/min |
| `POST /v1/plan/travel-summary-from-history` | 從瀏覽紀錄開場 | 10/min |
| `POST /v1/plan/recommend-city` | 城市推薦多輪對話 | 30/min |
| `POST /v1/plan/travel-guide` | 一次性產出完整行程 | 15/min |
| `POST /v1/plan/travel-revise` | 自然語言修改行程 | 20/min |
| `GET /debug/*` | 平台驗證用（sleep/memory/storage），不屬 App 契約 | — |

**共通契約**（source-spec 2.3 / 6.5）：LLM 軟失敗回 HTTP 200 + `fail_reason` 有值 + 可渲染兜底；驗證失敗 400（`110001`）；限流 429；share-image 快取過期回 200 + `metadata.status=C007`。原始服務未實作的：legacy share-image v1（刻意不移植）。

## 開發

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # 首次
cp .env.example .env                                          # 填入金鑰
.venv/bin/uvicorn app.main:app --reload                       # 開發伺服器
.venv/bin/pytest                                              # 測試（mock LLM；Neon 整合測試需 DATABASE_URL）
.venv/bin/pytest -m prompt_regression                         # 真實 LLM 回歸測試（耗額度，手動跑；注意 Groq TPM，測項間隔 60-90s）
```

## 帳號與金鑰設定（一次性精靈）

```bash
./scripts/setup-wizard.sh        # GitHub / GCP / Neon / Gemini / Groq / 首次部署
./scripts/cloudflare-wizard.sh   # Cloudflare Workers AI（階段 8 產圖）
```

皆可 Ctrl-C 中斷後重跑，已填的值會保留。

## 部署

Push 到 `main` → GitHub Actions 測試 → `gcloud run deploy --source .`（`.github/workflows/deploy.yml`）。

Repo secrets：`GCP_SA_KEY`、`GCP_PROJECT_ID`、`GEMINI_API_KEY`、`GROQ_API_KEY`、`DATABASE_URL`、`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`。

> ⚠️ deploy.yml 的 `--update-env-vars` 用 `^##^` 當分隔符——**不要改回 `^@^`**，`DATABASE_URL` 含 `@` 會被切斷（踩過，見 migration-plan 階段 6 備註）。

環境變數一覽（本地 `.env` 同名）：

| 變數 | 用途 |
|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` | 備援 LLM provider（主力已切 Vertex AI，ADC 認證不需金鑰） |
| `DATABASE_URL` | Neon（KV 快取/鎖/gallery；沒設會退回記憶體版） |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | 產圖 |
| `LLM_TRAVEL_SUMMARY` 等 `LLM_*` | 各 API 的模型路由覆寫，格式 `provider:model[:reasoning_effort]` |

## 監控

- **每請求 access log**（method/path/status/duration_ms）→ Cloud Run Logs Explorer，可建 log-based metrics
- **LLM 失敗**：回應的 `fail_reason` + service 層 error log；**產圖失敗**：`asset generation failed` warning log
- **延遲/錯誤率/instance 數**：Cloud Run console 內建 metrics 頁
- 額度儀表板：Groq console（TPM/RPD）、Cloudflare dashboard（neurons）、Neon console（storage）

## 已知限制

- **額度**：LLM 走 Vertex AI 計入 GCP 帳單（試用額度 $300 內免費，額度頁可查餘額）；Cloudflare 10,000 neurons/天 ≈ 18 次測驗產圖
- **單 instance 假設**：rate limiter 是記憶體版（多副本時各自計數）；max-instances 已設 1
- **產圖降級**：不支援參考圖 → 無旅伴人物合成、風格一致性靠 prompt 文字；stamp/tag 硬約束遵循待調校（migration-plan 階段 8 備註）
- **prompt 調校待辦**：集中記錄在 `docs/migration-plan.md` 各階段備註（destination 偶回國家/英文、字數規格遵循弱等）——都是換免費模型後的已知品質落差，功能可用
