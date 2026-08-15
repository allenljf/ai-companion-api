# 免費層可用的圖片生成 API 選型調研(階段 8 / share-image-v2)

> **查證日期:2026-08-15。** 免費層條款改版頻繁,所有數字以當日官方頁面為準;實作前(階段 8 開工日)應快速重驗首選方案的額度數字。標注「實測」者為本日以 curl 實打的結果。

## 1. TL;DR

| | 方案 | 一句話理由 |
|---|---|---|
| **首選** | **Cloudflare Workers AI**(hero 用 `flux-2-klein-4b` 直式、stamp/tag 用 `flux-1-schnell` 方圖) | 唯一「真 API 免費層 + 具體數字 + 免綁卡」的組合:每天 10,000 neurons,估可支撐約 18 次測驗(90 張)/天,base64 回傳,直式解析度可行 |
| **次選** | **Pollinations.ai**(匿名端點,現役模型 sana) | 零成本、免 API key、實測 2.7 秒回圖;但解析度上限約 1024(1152×2048 會被砍半成 576×1024)、模型不可選、服務條款極不穩定 |
| **付費備援** | **DeepInfra `FLUX.1-schnell`** | 無免費層但極便宜($0.0005/1024² 4 步),100 張/天全月約 $3–5,額度爆掉時的逃生口 |

**重要否定結論:**
- **Gemini API 免費層「不含」任何圖片生成模型**——官方定價頁對 `gemini-2.5-flash-image`、`gemini-3.1-flash-image`、Imagen 4 全部標示 Free Tier「Not available」。本專案那把 prepay credits 耗盡的 Gemini 金鑰,**無論免費層帳務是否獨立,都無法免費產圖**。
- **OpenRouter 的 `:free` 圖片模型已消失**——2026-08-15 實打 `GET /api/v1/models`,具 image 輸出的模型全為付費(gemini-3.1/2.5 image 系列、gpt-5 image 系列),無任何 `:free` 變體。
- **Together AI 的 FLUX.1 [schnell] Free 端點實質下架**——官方模型頁標示「This model is not available on Together's Serverless API」/「Launching soon」。

---

## 2. 比較表

| 服務 / 模型 | 免費額度 | 參考圖輸入 | 直式 1152×2048 | 延遲 | 回傳格式 | 限流 | 綁卡 |
|---|---|---|---|---|---|---|---|
| **Cloudflare Workers AI**<br>`flux-1-schnell` | 10,000 neurons/天(00:00 UTC 重置);1024² 4 步約 57.6 neurons ≈ 173 張/天 | ✗(參數只有 prompt/steps/seed) | ✗(schema 無 width/height,輸出固定方圖) | 數秒(官方未明示;社群普遍回報 2–5s) | base64(JSON `image` 欄位) | 以 neurons 計量;per-model RPM 官方未逐一明示 | **免**(Workers Free plan) |
| **Cloudflare Workers AI**<br>`flux-2-klein-4b` | 同上池;1152×2048 估約 313 neurons/張 ≈ 32 張/天(僅 hero) | 定價含「input 512×512 tile」單價、官方描述「unifies image generation and editing」→ 應支援,但**參數 schema 官方未明示** | 推定可(tile 計價 = 可變尺寸;上限官方未明示) | 官方稱 ultra-fast(數字未明示) | base64 | 同上 | 免 |
| **Cloudflare Workers AI**<br>`lucid-origin`(Leonardo) | 同上池;但 1152×2048 約 12 tiles × 636 neurons ≈ 7,600+ neurons ≈ **1 張/天** | ✗ | ✓(width/height 上限 2500) | 官方未明示 | base64 | 同上 | 免 |
| **Pollinations.ai**(匿名) | 無限額、免 key;文件稱匿名 1 req/15s | 文件寫 kontext 支援 `image` 參數,但**實測 kontext/nanobanana 已鎖 enter.pollinations.ai 付費平台** | △ 比例可指定但**上限約 1024**:實測要 1152×2048 只回 576×1024 | **實測 2.7–2.9s** | 直接回 JPEG binary | 匿名 15s/req(文件);實測連打 2 req 未被擋 | 免 |
| **Gemini API**(Google 直連) | **免費層不含圖片生成**(所有 image 模型 Free Tier = Not available) | ✓(Flash/Pro 最多 14 張參考圖,人物一致性/風格參照) | ✓(9:16、2:3 等 10 種比例;0.5K–4K) | 官方未明示(社群回報約 10–20s) | base64(inline data) | 付費層依 tier;IPM 制 | **需啟用付費** |
| **OpenRouter** | `:free` 圖片模型**已不存在**(實測 models API);免費模型通則:20 RPM、無儲值 50 req/天、儲值滿 $10 後 1000 req/天 | ✓(`/api/v1/images` 的 `input_references`,URL 或 base64) | ✓(`aspect_ratio: "9:16"` + `resolution` 參數) | 依上游 | base64(`b64_json`) | 20 RPM(free 通則) | 付費模型需儲值 |
| **Together AI**<br>FLUX.1 schnell Free | **實質下架**(「not available on Serverless API」) | — | — | — | — | — | — |
| **Hugging Face Inference Providers** | 免費帳號 **$0.10/月** 抵用金(官方明示 subject to change);PRO($9/月)$2.00/月 | 依 provider | 依 provider | 依 provider | 依 provider | 額度極小,$0.10 約 20–30 張 schnell 級 → **不敷使用** | 免(超額才要) |
| **fal.ai** | 定價頁**無免費層**;僅第三方提及少量註冊贈點 | ✓(付費模型) | ✓ | 快(付費) | URL/base64 | — | 需 |
| **DeepInfra** | 無免費層;`FLUX.1-schnell` **$0.0005 × (w/1024) × (h/1024) × iters** | schnell ✗ | ✓(可變 w/h;hero 4 步約 $0.0045/張) | 數秒 | URL/base64 | — | 需儲值 |

> neurons 估算說明:Cloudflare 官方定價為 flux-1-schnell「$0.0000528/512² tile + $0.0001056/step」(= 4.80 + 9.60 neurons)、flux-2-klein-4b「$0.000059/input tile + $0.000287/output tile」(= 5.37 + 26.1 neurons)、換算率 $0.011/1,000 neurons。tile 數以 `ceil(寬/512) × ceil(高/512)` 估(1024²=4 tiles、1152×2048=12 tiles);**官方未明示切 tile 進位規則**,實作時應打一張後從 response header / dashboard 核對實際扣量。

---

## 3. 各候選細節

### 3.1 Cloudflare Workers AI —— 首選

- **免費額度**:Free 與 Paid Workers plan 都含 **10,000 neurons/天**,每日 00:00 UTC 重置,超出後 Free plan 直接擋(不會自動扣款),**無需綁卡**。來源:<https://developers.cloudflare.com/workers-ai/platform/pricing/>
- **現役 text-to-image 模型**(2026-08-15 官方目錄):`flux-1-schnell`、`flux-2-dev`(multi-reference)、`flux-2-klein-4b`、`flux-2-klein-9b`、`lucid-origin`、`phoenix-1.0`(Leonardo)、SDXL 系列(多為 Beta/舊)。來源:<https://developers.cloudflare.com/workers-ai/models/?tasks=Text-to-Image>
- **`flux-1-schnell`**(<https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/>):
  - 參數僅 `prompt`(1–2048 字元)、`steps`(預設 4、上限 8)、`seed`。**無 width/height → 只能出方圖**,適合 stamp/tag。
  - 回傳 JSON `image` 欄位 base64(JPEG)。
  - 成本:1024² × 4 步 ≈ 57.6 neurons → 免費層一天約 **173 張**。
- **`flux-2-klein-4b`**(<https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/>):
  - 官方描述「unifies image generation **and editing** in a single model」,定價含 input tile 單價 → 應收圖片輸入;但**輸入 schema(width/height 上限、image 參數名)官方頁面未展開,屬未明示**,開工第一件事就是實打驗證。
  - hero 1152×2048 估 12 output tiles ≈ 313 neurons/張。
- **本專案用量試算**:每次測驗 = 1 hero(klein-4b,313)+ 4 張方圖(schnell,4×57.6=230)≈ **543 neurons → 約 18 次測驗(90 張)/天**,正好覆蓋「20–100 張/天」需求;若 hero 也退回 schnell 方圖再裁切,可到 170+ 張/天。
- **呼叫方式**:REST `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell`,httpx server-to-server 完全沒問題。
- **限流**:官方以 neurons 計量為主,per-model RPM 未逐一公告;本專案量級(尖峰每分鐘個位數)不構成風險。

### 3.2 Pollinations.ai —— 次選(零成本,但已「變質」)

- **現況與官方文件嚴重不一致**。GitHub 官方 APIDOCS(<https://raw.githubusercontent.com/pollinations/pollinations/master/APIDOCS.md>)仍寫著 flux 預設、kontext 支援 `image` 參數、匿名 1 req/15s / Seed 註冊 1 req/5s;但 **2026-08-15 實測**:
  - `GET https://image.pollinations.ai/models` 只回 `["sana"]`;
  - 指定 `model=flux` 實際仍由 **sana** 出圖(EXIF manufacturer=sana);
  - `model=nanobanana` 回 500:「nanobanana model is only available on enter.pollinations.ai」→ 好模型已遷往新的付費/註冊平台 enter.pollinations.ai(JS dashboard,額度數字無法無登入查證,**官方未明示**)。
- **實測表現**(匿名、免 key):576×1024 直式 2.7s / HTTP 200 / 直接回 JPEG binary;請求 1152×2048 會**保比例縮到 576×1024**(上限約 1024 邊長);測試圖無可見浮水印;動漫街景品質可接受(sana 風格偏寫實插畫)。
- **結論**:當「零成本 demo 備胎」可以,當正式方案不行——模型不可控、尺寸不足(576×1024 需後端 2x upscale 或前端拉伸)、條款隨時再變。

### 3.3 Gemini API(Google 直連)—— 免費層不可用,付費層是品質天花板

- **免費層明確不含圖片生成**:官方定價頁(<https://ai.google.dev/gemini-api/docs/pricing>)Gemini 2.5 Flash Image、Gemini 3.1 Flash Image、Imagen 4 的 Free Tier 全標「Not available」。rate-limits 頁(<https://ai.google.dev/gemini-api/docs/rate-limits>)也只對 image 模型列 IPM(付費層概念)。
- **回答本專案的特別疑問**:金鑰 prepay credits 耗盡 → 文字模型 429,而圖片模型**從來就不在免費層**,所以「免費層帳務是否與 prepay 獨立」對產圖無意義——**要用 Gemini 產圖就是要付錢**(2.5 Flash Image $0.039/張;3.1 Flash Image $0.045/0.5K 起;Imagen 4 Fast $0.02/張)。
- 若未來願意付費,它是功能最完整的:`gemini-3.1-flash-image` 支援 **最多 14 張參考圖**(物件 + 風格參照、人物一致性)、10 種長寬比含 9:16、0.5K–4K、base64 回傳(<https://ai.google.dev/gemini-api/docs/image-generation>)——hero 的「風格參考圖 + 旅伴頭像」原始需求只有這一級模型做得到。

### 3.4 OpenRouter —— 免費圖片模型已消失

- 2026-08-15 實打 `GET https://openrouter.ai/api/v1/models`,輸出含 image modality 的模型:`google/gemini-3.1-flash-lite-image`、`google/gemini-3.1-flash-image(-preview)`、`google/gemini-3-pro-image(-preview)`、`google/gemini-2.5-flash-image`、`openai/gpt-5-image(-mini)`、`openai/gpt-5.4-image-2`——**全部付費,無 `:free`**。網站上仍搜得到 `gemini-2.5-flash-image-preview:free` 的模型頁,但該 id 已不在 models API 清單,視為殘頁。
- 若付費,其 `/api/v1/images` 端點介面很適合本專案:`input_references`(URL 或 base64 參考圖)、`aspect_ratio: "9:16"`、`resolution: "2K"`、回傳 `b64_json`(<https://openrouter.ai/docs/guides/overview/multimodal/image-generation>)。免費模型通則限流(20 RPM;無儲值 50 req/天、儲值滿 $10 終身 1000 req/天)目前只適用文字 `:free` 模型(<https://openrouter.ai/docs/api-reference/limits>)。

### 3.5 Together AI —— 免費 FLUX schnell 已下架

- 官方模型頁(<https://www.together.ai/models/flux-1-schnell>)標示「This model is not available on Together's Serverless API」+「Launching soon / We'll email you when the endpoint goes live」。當年「3 個月免費 FLUX.1 [schnell]」活動(<https://www.together.ai/blog/flux-api-is-now-available-on-together-ai-new-pro-free-access-to-flux-schnell>)已結束。**不納入。**

### 3.6 Hugging Face Inference Providers —— 額度太小

- 官方定價文件(<https://huggingface.co/docs/inference-providers/en/pricing>):免費帳號每月 **$0.10** 抵用金(「subject to change」)、PRO $2.00/月,走 HF 路由按 provider 原價扣。$0.10 大約只夠 20–30 張 schnell 級圖/「月」,對每天 20–100 張的需求**不可行**;僅適合當多 provider 統一介面的付費通道。

### 3.7 fal.ai / DeepInfra —— 無免費層,但為最佳付費備援

- **fal.ai**:官方定價頁(<https://fal.ai/pricing>)純 pay-per-use,無免費層(第三方提及少量一次性註冊贈點,官方未明示)。FLUX Kontext Pro $0.04/張。
- **DeepInfra**:官方定價頁(<https://deepinfra.com/pricing>)無免費層;`FLUX.1-schnell` **$0.0005 × (w/1024) × (h/1024) × iters** → hero 1152×2048 × 4 步 ≈ $0.0045、方圖 1024² ≈ $0.002。每天 100 張混合用量全月約 **$3–5**,是免費層失效時成本最低的逃生口,且支援任意 w/h(直式 OK)。

---

## 4. 對照 migration-plan 3.3 降級方案的建議

migration-plan 3.3 的降級三條:① hero 改純文字 prompt(放棄風格參考圖與旅伴合成)② 風格一致性改靠 prompt 文字描述強化 ③ 旅伴人物合成直接砍掉。

**首選(Cloudflare Workers AI)下的結論:**

1. **hero 風格參考圖:降級確定發生。** `flux-1-schnell` 無圖片輸入;`flux-2-klein-4b` 定價與描述暗示支援 editing/參考圖,但參數官方未明示——**階段 8 第一個 spike 就是實打 klein-4b 驗證 image 輸入**。驗證失敗即走降級 ①+②:art_style prompt(`reference/prompt-fixtures/` 既有素材)全部轉成文字風格描述,靠固定 seed + 固定風格前綴撐一致性。
2. **旅伴人物合成:建議直接砍(降級 ③)。** 就算 klein-4b 收參考圖,4B 蒸餾模型的人物一致性也遠不及 Gemini 的 14 張參考圖機制;這是原規劃就標記「最難且非核心」的部分。若日後想撿回來,唯一務實路徑是付費 `gemini-3.1-flash-image`(每次測驗 1 張 hero,月成本個位數美元)。
3. **直式 hero:兩層策略。** 主路徑 klein-4b 出 1152×2048(尺寸上限待實打確認);fallback 用 schnell 出 1024² 方圖後由後端裁切/外推(share-image-v2 本來就由後端合成文字版面,方圖素材 + 版面設計上下延伸是可接受的視覺降級)。
4. **stamp / tag(4 張方圖)**:`flux-1-schnell` 完全夠用,一張約 57.6 neurons、數秒回 base64,與軟失敗架構(逐素材獨立成敗)天然相容。
5. **逐素材 fallback 分類**:Pollinations(sana)可當「Cloudflare 額度用盡/故障時」的第三層兜底——免 key、2.7s、直接回 JPEG,但只出 ≤1024 邊長,fallback 素材品質標記 `fail_reason` 或降級欄位讓前端知道。

## 5. 風險註記

| 風險 | 影響 | 對策 |
|---|---|---|
| Cloudflare neurons 計價/切 tile 規則未官方明示細節,估算可能偏差 ±數十 % | 每日可產張數低於預估 | 上線首日以 dashboard 實際扣量校正;超量時 fallback 到 Pollinations 或排隊到隔日 |
| `flux-2-klein-4b` 為新上架模型,schema 未文件化,參數/尺寸上限可能與假設不符 | hero 直式方案不成立 | spike 先行;fallback = schnell 方圖 + 後端版面補償 |
| Pollinations 條款極不穩定(2026 已實質收攏好模型到 enter.pollinations.ai,匿名端點只剩 sana) | 第三層兜底隨時失效 | 只當兜底、不當主路徑;每次部署前 smoke test 一張 |
| Cloudflare 免費 neurons 額度本身可能改版(曾多次調整計價) | 主路徑額度縮水 | provider 抽象已是本專案既定架構,Image Client 保持可切換;DeepInfra($3–5/月)為付費逃生口 |
| Gemini 免費層未來若把 image 模型加回(Google 曾在 2025 預覽期短暫提供) | 機會而非風險 | 階段 8 開工時重查 <https://ai.google.dev/gemini-api/docs/pricing> 一次 |
| Together「Launching soon」若真回歸免費 schnell | 機會 | 不等待、不依賴;回歸後再評估 |

## 附:主要來源

- Gemini 定價:<https://ai.google.dev/gemini-api/docs/pricing>;能力:<https://ai.google.dev/gemini-api/docs/image-generation>;限流:<https://ai.google.dev/gemini-api/docs/rate-limits>
- Cloudflare 定價:<https://developers.cloudflare.com/workers-ai/platform/pricing/>;模型:<https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/>、<https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/>、<https://developers.cloudflare.com/workers-ai/models/lucid-origin/>
- Pollinations 官方文件:<https://raw.githubusercontent.com/pollinations/pollinations/master/APIDOCS.md>(與實測不一致,以實測為準)
- OpenRouter:models API 實測、<https://openrouter.ai/docs/api-reference/limits>、<https://openrouter.ai/docs/guides/overview/multimodal/image-generation>
- Together:<https://www.together.ai/models/flux-1-schnell>
- Hugging Face:<https://huggingface.co/docs/inference-providers/en/pricing>
- fal.ai:<https://fal.ai/pricing>;DeepInfra:<https://deepinfra.com/pricing>
