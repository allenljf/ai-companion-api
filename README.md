# AI Companion API

把 kkday-b2c-api 的「AI 旅伴」（Phase 1 + Phase 2 共 17 支 API）重寫成獨立的 Python / FastAPI 雲端服務。規格見 `docs/source-spec.md`，路線圖與進度見 `docs/migration-plan.md`。

## 技術選型（2026-08-14 定案）

| 項目 | 選擇 | 備註 |
|---|---|---|
| Hosting | **Google Cloud Run**（asia-east1） | timeout 設 300s（travel-guide 需 ≥60s）、min-instances 0、免費層每月 200 萬請求 |
| DB | **Neon Postgres**（免費層 0.5GB） | 取代原設計的 SQLite，見下方結論 |
| LLM | **Gemini Flash**（重度結構化）+ **Groq**（輕量判讀） | provider 抽象、每支 API 可獨立指定模型 |
| 產圖 | **Gemini 2.5 Flash Image**（主）/ Cloudflare Workers AI FLUX（備） | Gemini 支援參考圖輸入，覆蓋 hero 風格一致性需求 |

### SQLite 可用性結論（階段 0 驗收項）

**SQLite 不可用，改用 Neon Postgres。** 理由：Cloud Run 的檔案系統是記憶體內的暫時檔案系統（instance 回收即消失），且可能同時跑多個 instance，違反 SQLite「單一 instance + 持久磁碟」的前提（`docs/migration-plan.md` 第 2 章的警告條件全部命中）。cache / gallery / lock 三張表改建在 Neon 上，`app/storage/kv.py` 的抽象介面不變。

## 開發

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # 首次
cp .env.example .env                                          # 填入金鑰
.venv/bin/uvicorn app.main:app --reload                       # 開發伺服器
.venv/bin/pytest                                              # 測試（mock LLM）
.venv/bin/pytest -m prompt_regression                         # 真實 LLM 回歸測試（耗額度，手動跑）
```

## 帳號與金鑰設定（一次性）

```bash
./scripts/setup-wizard.sh
```

互動式引導完成：GitHub repo、GCP 專案/計費/API/Service Account、Neon、Gemini、Groq、`data/ai_partner.json` 抓取、首次部署與驗收（`/health`、`/debug/sleep?seconds=45`）。可 Ctrl-C 中斷後重跑，已填的值會保留。

## 部署

Push 到 `main` → GitHub Actions 跑測試 → `gcloud run deploy --source .` 部署到 Cloud Run（見 `.github/workflows/deploy.yml`）。需要 repo secrets：`GCP_SA_KEY`（Service Account JSON）、`GCP_PROJECT_ID`。

環境變數（Cloud Run 上用 `gcloud run services update ai-companion-api --set-env-vars` 或 console 設定）：

| 變數 | 用途 | 需要的階段 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini 推理與產圖 | 階段 1 起 |
| `GROQ_API_KEY` | Groq 輕量任務 | 階段 1 起 |
| `DATABASE_URL` | Neon Postgres | 階段 6 起 |

## 已知限制

- `data/ai_partner.json` 目前是佔位資料，需以原始服務 `GET /api/v3/companion/ai-partner` 回應的 `data` 欄位覆蓋（wizard 第 8 步可代抓）。
- Cloud Run scale-to-zero 冷啟動實測值待補（閒置 30 分鐘後 `time curl <URL>/health`）。
- `/debug/*` 端點不屬於 App 契約，僅供平台驗證。
