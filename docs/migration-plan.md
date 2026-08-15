# AI 旅伴獨立雲端服務 — 遷移計畫

> **目標**：把 kkday-b2c-api 的 AI 旅伴（Phase 1 + Phase 2，共 17 支 API）搬成自己的獨立雲端服務。
> **技術決策**：Python / FastAPI、免費 hosting、免費 LLM、免費圖片生成。
> **執行方式**：多 session 分階段完成，本文件是跨 session 的唯一依據。
> **原始規格**：`docs/source-spec.md`（1733 行，含全部 API 規格與 prompt 全文）
> **PHP 實作快照**：`reference/`（唯讀查證用，見 `reference/README.md`）
> **AI 工作指引**：`CLAUDE.md`（新對話會自動載入）

---

## 目錄

1. [先讀：三個會決定成敗的判斷](#1-先讀三個會決定成敗的判斷)
2. [目標架構](#2-目標架構)
3. [技術選型檢查清單（你要自己確認的）](#3-技術選型檢查清單)
4. [專案結構與關鍵實作模式](#4-專案結構與關鍵實作模式)
5. [分階段路線圖（9 個階段）](#5-分階段路線圖)
6. [Prompt 移植與調校方法](#6-prompt-移植與調校方法)
7. [開發工作流：怎麼用 SDD 跨 session 推進](#7-開發工作流)
8. [風險清單與 fallback](#8-風險清單與-fallback)

---

## 1. 先讀：三個會決定成敗的判斷

### 判斷一：只移植 share-image-v2，放棄 legacy v1

原始服務有兩套產圖：

| | v1（legacy） | v2（建議串接版本） |
|---|---|---|
| 產出 | 整張含**繁體中文字**的海報 | **無文字** hero 場景圖 + 裝飾素材，文字由 App 合成 |
| 換免費模型後 | ❌ **不可行**——免費圖片模型渲染中文幾乎必然變亂碼方塊 | ✅ **可行**——本來就不需要模型寫字 |

v2 當初是為了「把 2-3 分鐘壓到 30 秒」而做的，副作用是它**天生相容於不會寫字的模型**。這是整個「含產圖」範圍能成立的前提。

**決定：新服務只實作 v2 產圖路徑，v1 直接不移植。**

### 判斷二：產圖是全案最大風險，把它排在最後

原始的 v2 產圖不只是「呼叫一次 API」，它包含：

- 三種素材（hero / stamp / tag×3）各有不同的 prompt 與硬約束
- hero 要帶**參考圖**（風格參考 + 可選的旅伴外觀）→ 需要 image-to-image 或多圖輸入能力
- S3 冪等（key 含 prompt SHA-256）、併發鎖、跨用戶共用快取
- 逐素材獨立成敗 + fallback 分類降級

免費圖片服務**大多不支援參考圖輸入**，或支援但品質不穩。這代表 hero 的「風格一致性」與「旅伴人物合成」很可能要降級或放棄。

**決定：產圖放在階段 8（倒數第二），前面 7 個階段完成時服務已經有 14 支 API 能用。就算產圖最後做不出理想效果，專案也已經有價值。**

### 判斷三：真正的資產是 prompt，不是程式碼

程式碼翻寫是機械工作（PHP → Python 大約 1:1）。真正花時間的是：**這些 prompt 是針對 GPT-5.6 調校的，換模型後要重新調**。

原始 prompt 的要求相當苛刻：
- 嚴格純 JSON 輸出（不能有 markdown 框、不能有解說文字）
- 繁體中文（很多開源模型簡中傾向嚴重）
- 同時遵守 10+ 條約束（`travel-guide` 有 11 條規則 + 2 個條件注入段）
- 位置索引對映不能錯位（`product_indexes` 錯一個就整包失效）

**預估：prompt 重新調校佔全案 30-40% 工時。** 這不是「翻譯完就好」的工作，要有反覆實測的心理準備。第 6 章給了具體方法。

---

## 2. 目標架構

```
                    ┌─────────────────────────────┐
   App / 測試工具 ──▶│  FastAPI (單一容器)          │
                    │                              │
                    │  ├─ /v1/companion/*  (P1 七支)│
                    │  └─ /v1/plan/*       (P2 七支)│
                    │                              │
                    │  ├─ LLM Client（provider 抽象）│──▶ 免費 LLM API
                    │  ├─ Image Client（provider 抽象）│──▶ 免費圖片生成 API
                    │  ├─ Normalize / Whitelist    │
                    │  └─ Prompt Templates（檔案）  │
                    └──────┬───────────────┬───────┘
                           │               │
                     ┌─────▼─────┐   ┌─────▼──────┐
                     │ KV/Cache  │   │ 物件儲存    │
                     │ (SQLite   │   │ (R2/B2/    │
                     │  或 Redis)│   │  Supabase) │
                     └───────────┘   └────────────┘
```

**與原始服務的差異（刻意的簡化）**：

| 原始 | 新服務 | 理由 |
|---|---|---|
| DCS 遠端設定（prompt 熱更新） | **prompt 檔案 + 啟動時載入**（或加一個受保護的 reload 端點） | 自己的服務不需要跨團隊的設定平台；檔案更好 diff、更好版控 |
| Redis（快取 + gallery + 鎖） | **SQLite 單檔**（三張表：cache / gallery / locks） | 免費單機部署下，SQLite 完全夠用，少一個外部依賴 |
| S3 + CDN | **物件儲存 + 其內建公開 URL** | 同性質，換供應商 |
| PHP 全站錯誤碼體系 | **保留軟失敗契約**，錯誤碼精簡 | 軟失敗（HTTP 200 + `fail_reason`）是核心設計，一定要保留 |

> ⚠️ **SQLite 的前提是單一 instance**。如果 hosting 會跑多副本或有持久化磁碟限制（很多免費容器方案的檔案系統是暫時的、重啟即消失），就要改用外部 KV。這點在階段 0 就要驗證清楚。

---

## 3. 技術選型檢查清單

> ⚠️ 我不列具體供應商的方案內容——這類條款變動很快，寫死在文件裡很快就過期。以下是**你要親自去確認的檢查項**，用這些條件去篩。

### 3.1 Hosting（最關鍵的是 timeout，不是流量）

必須確認：

- [ ] **單一請求的 timeout 上限 ≥60 秒**（`travel-guide` 要 5~30 秒，產圖更久）——很多免費 serverless 只給 10 秒，直接出局
- [ ] **是否 scale-to-zero？冷啟動要多久？**（若 >30 秒，第一個使用者體驗會很糟；可用定時 ping 保溫，但要算進流量額度）
- [ ] **記憶體上限**（Python + FastAPI 大約要 150-250MB，含相依套件）
- [ ] **檔案系統是否持久**（決定 SQLite 能不能用）
- [ ] **是否會跑多副本**（決定 SQLite 能不能用、併發鎖要不要外部化）
- [ ] 流量/執行時間額度是否夠你的測試與展示用量

### 3.2 LLM Provider

必須確認：

- [ ] **繁體中文輸出品質**（拿 `travel-guide` 的 prompt 實測，看會不會吐簡中）
- [ ] **JSON 格式遵循度**（有無 JSON mode / structured output 功能會差很多）
- [ ] **免費層的 RPM / RPD / TPM 限制**（`travel-guide` 單次 8000 tokens，額度消耗快）
- [ ] **context window ≥16K**（`travel-revise` 要塞完整行程 + 完整對話）
- [ ] 是否支援指定 JSON schema（有的話能大幅減少 parse 失敗）

**建議策略：混用兩個 provider。**

| 任務類型 | API | 需求 |
|---|---|---|
| 輕量判讀 | `travel-summary`、`from-orders`、`from-wish`、`from-history`、`self-introduction` | 速度優先，模型可以弱一點 |
| 重度結構化 | `travel-guide`、`travel-revise`、`quiz-completions` | 品質優先，要能穩定吐大 JSON |
| 對話 | `recommend-city` | 中等 |

所以 LLM client 一定要做**provider 抽象 + 每支 API 可獨立指定模型**（這也對應原始服務的 luna/terra 分流設計）。

### 3.3 圖片生成（風險最高）

必須確認：

- [ ] **是否支援參考圖輸入**（image-to-image / 多圖）——hero 的風格一致性靠這個
- [ ] **輸出解析度**（原始 hero 是 1152×2048 直式；若只支援正方形要重新設計版面）
- [ ] **免費額度與速率**（一次測驗要產 5 張：hero + stamp + tag×3）
- [ ] **產圖耗時**（會決定輪詢節奏）
- [ ] 是否回 base64 或 URL（回 URL 的話可省掉自己存圖）

**降級方案（若參考圖不支援）**：
1. hero 改用純文字 prompt（放棄風格參考圖與旅伴人物合成）
2. 風格一致性改靠 prompt 文字描述強化（效果較差但可接受）
3. 旅伴人物合成功能**直接砍掉**（這是最難的部分，且非核心）

### 3.4 物件儲存

必須確認：

- [ ] 免費容量（一張 hero 約 1-3MB，測試階段幾百張就好）
- [ ] **egress（流出流量）是否免費**——這是隱藏成本最大的一項
- [ ] 是否能產生公開可存取的 URL（App 要直接載入）

---

## 4. 專案結構與關鍵實作模式

### 4.1 目錄結構

```
ai-companion-api/
├── app/
│   ├── main.py                      # FastAPI app + 路由掛載
│   ├── config.py                    # pydantic-settings（env 讀取）
│   ├── api/
│   │   ├── deps.py                  # 依賴注入
│   │   └── v1/
│   │       ├── companion.py         # Phase 1 七支路由
│   │       └── plan.py              # Phase 2 七支路由
│   ├── schemas/
│   │   ├── common.py                # 回應信封、軟失敗
│   │   ├── companion.py             # Phase 1 request/response
│   │   ├── plan.py                  # Phase 2 request/response
│   │   └── itinerary.py             # 行程 day/item（兩期共用）
│   ├── services/
│   │   ├── llm/
│   │   │   ├── client.py            # 抽象介面 + 路由到 provider
│   │   │   └── providers/           # 各家實作
│   │   ├── image/
│   │   │   ├── client.py
│   │   │   └── providers/
│   │   ├── companion/               # Phase 1 業務邏輯
│   │   │   ├── quiz.py
│   │   │   ├── completion.py
│   │   │   ├── identity_mapper.py   # 八人格規則式判定（純函式）
│   │   │   ├── share_image.py
│   │   │   └── gallery.py
│   │   └── plan/                    # Phase 2 業務邏輯
│   │       ├── summary.py
│   │       ├── summary_from_orders.py
│   │       ├── summary_from_wish.py
│   │       ├── summary_from_history.py
│   │       ├── recommend_city.py
│   │       ├── guide.py
│   │       └── revise.py
│   ├── core/
│   │   ├── normalize.py             # ★ 行程 normalize + 白名單（最核心）
│   │   ├── soft_fail.py             # 軟失敗統一處理
│   │   ├── json_parse.py            # decodeLlmJson 等價物
│   │   └── truncate.py              # 句尾截斷
│   ├── prompts/                     # ★ prompt 模板（純文字檔）
│   │   ├── phase1/
│   │   │   ├── quiz_rewrite.txt
│   │   │   ├── quiz_completion.txt
│   │   │   ├── self_introduction.txt
│   │   │   └── image_*.txt          # 產圖 art_style / hero / stamp / tag
│   │   └── phase2/
│   │       ├── summary_quiz.txt / summary_order_pick.txt / summary_import.txt
│   │       ├── from_orders.txt / from_wish.txt / from_history.txt
│   │       ├── recommend_city.txt   # 含條件段
│   │       ├── guide.txt
│   │       └── revise.txt
│   └── storage/
│       ├── kv.py                    # cache / lock（SQLite 或 Redis）
│       └── objects.py               # 圖片上傳
├── tests/
│   ├── unit/                        # normalize / mapper / prompt 組裝（無 LLM）
│   ├── integration/                 # mock LLM 的端到端
│   └── prompt_regression/           # ★ 真實 LLM 的 prompt 回歸測試
├── data/
│   ├── quiz_dimensions.json         # 原 DCS ai_quiz.dimensions
│   ├── ai_partner.json              # 原 DCS ai_partner
│   └── fake/                        # orders / wish_list / history 假資料
├── pyproject.toml
├── Dockerfile
└── README.md
```

### 4.2 關鍵模式一：Pydantic 要「強制轉型或給預設」，不能拋錯

**這是 Python 版最重要的實作細節。** 原始 PHP 的 normalize 哲學是「LLM 亂給就轉成安全預設，絕不拋錯」，但 Pydantic 預設行為是驗證失敗就 raise。

錯誤做法（會讓軟失敗變成 500）：

```python
class ItineraryItem(BaseModel):
    type: Literal["spot", "logistics", "meal"]   # ❌ LLM 給 "hotel" 就 raise
    time: str                                     # ❌ LLM 給 null 就 raise
```

正確做法（用 `mode="before"` 的 validator 做強制轉型）：

```python
from pydantic import BaseModel, field_validator
import re

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

class ItineraryItem(BaseModel):
    name: str | None = None
    text: str = ""
    type: str = "spot"
    time: str | None = None
    lat: float | None = None
    lng: float | None = None
    transport_mode: str | None = None
    oid: str | None = None
    prod_id: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v):
        return v if v in ("spot", "logistics", "meal") else "spot"

    @field_validator("time", mode="before")
    @classmethod
    def _coerce_time(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        return v if TIME_RE.match(v) else None

    @field_validator("transport_mode", mode="before")
    @classmethod
    def _coerce_transport(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        return v if v in ("walk", "bus", "train", "car") else None

    @field_validator("name", "oid", "prod_id", mode="before")
    @classmethod
    def _scalar_trim(cls, v):
        # 對應 PHP 的 scalarTrim()：非 scalar（LLM 亂給陣列）一律視為沒帶，避免炸掉
        if not isinstance(v, (str, int, float, bool)) or isinstance(v, bool):
            return None
        s = str(v).strip()
        return s or None
```

**白名單與條件性欄位移除**在 model 外做（因為需要外部 context）：

```python
def normalize_items(items: list[dict], allowed_oids: set[str],
                    allowed_prod_ids: set[str]) -> list[dict]:
    out = []
    for raw in items:
        item = ItineraryItem.model_validate(raw or {}).model_dump()

        # 白名單：只認輸入帶進來的 id，幻覺一律濾成 None
        item["oid"] = item["oid"] if item["oid"] in allowed_oids else None
        item["prod_id"] = item["prod_id"] if item["prod_id"] in allowed_prod_ids else None

        # 條件性欄位：非 spot 就整個 key 移除（不是給 None）
        if item["type"] != "spot":
            item.pop("lat", None); item.pop("lng", None)
        if item["type"] != "logistics":
            item.pop("transport_mode", None)

        out.append(item)
    return out
```

> ⚠️ **`lat`/`lng`/`transport_mode` 是「移除 key」不是「設 None」**——原始契約如此，App 端可能用 `key in item` 判斷。這個細節很容易在移植時搞錯。

### 4.3 關鍵模式二：LLM Client 抽象

```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, system: str, user: str, *,
                   max_tokens: int, model: str | None = None) -> str: ...

class LLMClient:
    """依任務名稱路由到不同 provider/model，對應原始的 luna/terra 分流。"""
    def __init__(self, providers: dict[str, LLMProvider], routing: dict[str, tuple[str, str]]):
        self._providers = providers
        self._routing = routing   # {"travel_guide": ("gemini", "gemini-x-pro"), ...}

    async def chat(self, task: str, system: str, user: str, *, max_tokens: int) -> str:
        provider_name, model = self._routing[task]
        return await self._providers[provider_name].chat(
            system, user, max_tokens=max_tokens, model=model
        )
```

**為什麼一定要抽象**：換 provider 是這個專案的高機率事件（免費方案會變、品質不如預期要換）。抽象層讓「換模型」變成改設定而不是改程式。

### 4.4 關鍵模式三：軟失敗要在 service 層統一

```python
@dataclass
class LLMResult:
    data: dict | None
    fail_reason: str | None

async def call_and_parse(task: str, system: str, user: str, max_tokens: int) -> LLMResult:
    try:
        raw = await llm.chat(task, system, user, max_tokens=max_tokens)
    except Exception as exc:
        logger.error("LLM failed", extra={"task": task, "error": str(exc)})
        return LLMResult(None, f"LLM fail: {type(exc).__name__}: {exc}")

    parsed = decode_llm_json(raw)     # 容忍 ```json 框
    if parsed is None:
        return LLMResult(None, "LLM fail: 回應無法解析為 JSON")
    return LLMResult(parsed, None)
```

**注意 timeout 設定**：`httpx.AsyncClient(timeout=httpx.Timeout(90.0))`——`travel-guide` 要 30 秒，預設 5 秒會全部失敗。

### 4.5 關鍵模式四：prompt 從程式碼抽到檔案

原始是 PHP heredoc 內嵌在 Service 裡。新服務改成獨立檔案 + `str.format()` 或 Jinja2：

```python
from pathlib import Path
from functools import lru_cache

PROMPT_DIR = Path(__file__).parent / "prompts"

@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")

def build_guide_prompt(persona: str, booked_section: str, product_section: str) -> str:
    return load_prompt("phase2/guide").format(
        persona=persona,
        booked_orders_section=booked_section,
        selected_products_section=product_section,
    )
```

**好處**（相對於原始實作是改善）：
- prompt 改動在 git diff 裡清楚可讀（不會混在程式碼 diff 裡）
- 可以做 prompt 版本 A/B（載入不同檔案）
- 調校時不用重啟（可加 reload 端點，或 dev 模式關掉 `lru_cache`）

> ⚠️ prompt 內含大量 `{` `}`（JSON 範例），用 `str.format()` 會衝突。**建議用 Jinja2**（`{{ }}` 為變數）或自訂 placeholder（如 `<<PERSONA>>`）。這個坑一定會踩，先決定好。

---

## 5. 分階段路線圖

> 每個階段的結束條件都是「**部署上去 + 用真實 LLM 打過 + 行為符合 spec**」，不是「程式碼寫完」。

### 階段 0：選型驗證與骨架（不要跳過）

**目標**：證明技術選型可行，而不是寫功能。

| 任務 | 驗收條件 |
|---|---|
| 依第 3 章檢查清單選定 hosting / LLM / 圖片 / 儲存，註冊帳號取得金鑰 | 四項都有可用金鑰 |
| FastAPI 骨架 + `/health` + 一支不打 LLM 的端點（建議 `GET /v1/companion/ai-partner`，讀 `data/ai_partner.json`） | 部署後從外網打得到 |
| **實測平台限制**：寫一支 `/debug/sleep?seconds=45` 驗證 timeout | 45 秒的請求沒有被平台切斷 |
| **實測冷啟動**：閒置 30 分鐘後再打 | 記錄實際冷啟動秒數 |
| **實測記憶體**：看平台的記憶體使用量 | 未接近上限 |
| 決定 SQLite 是否可用（檔案系統持久？多副本？） | 有明確結論寫進 README |
| Dockerfile + CI 自動部署 | push 後自動上線 |

**⚠️ 如果 timeout 過不了 60 秒，立刻換平台，不要往下做。** 這是唯一一個「不通就必須換選型」的關卡。

**工作量估**：1-2 個 session（大部分時間在申請帳號與測平台）

---

### 階段 1：LLM 抽象層 + 第一支 LLM API

**目標**：建立「組 prompt → 呼叫 → 解析 → 軟失敗」的基礎模式，後面 13 支都複製這個模式。

| 任務 | 驗收條件 |
|---|---|
| `LLMProvider` 抽象 + 至少一家實作 | 單元測試（mock httpx）通過 |
| `decode_llm_json()`（容忍 ` ```json ` 框） | 三種輸入格式測試通過 |
| `call_and_parse()` 軟失敗包裝 | LLM 拋錯時回 `fail_reason`、不 500 |
| prompt 載入機制 + placeholder 方案定案 | prompt 含 JSON 範例時不會炸 |
| **實作 `POST /v1/plan/travel-summary`**（四入口，`from_zero` 不打 LLM） | 四種 `entry_type` 都回正確結果；A/A2 的 `city` 原樣回傳（不經 LLM）|
| 回應信封 + 400 驗證錯誤格式 | 缺必填欄位回 400 而非 500 |

**移植重點**：`from_zero` 完全不打 LLM（固定文案）；`quiz_completion`/`from_orders` 的 `city` 是權威輸入，**prompt 的輸出 JSON 只有 `summary`，不含 `city`**。

**工作量估**：1-2 個 session

---

### 階段 2：三支城市判讀 API（位置索引模式）

**目標**：建立**位置索引對映**這個核心防呆模式。

| 任務 | 驗收條件 |
|---|---|
| `POST /v1/plan/travel-summary-from-orders` | `order_index` 由後端依位置指定；筆數不符回 `llm_error` |
| `POST /v1/plan/travel-summary-from-wish` | `product_indexes` → 後端解析成 `prod_id`；**prod_id 不進 prompt** |
| `POST /v1/plan/travel-summary-from-history` | 同上（獨立實作，不共用程式碼） |
| 索引解析的防呆 | 超範圍/非數字索引忽略；同索引只認第一次；城市去重；上限 3 |

**驗收要點**：寫一個測試「LLM 回傳超出範圍的 index」，確認不會 crash 也不會產出假 `prod_id`。

**工作量估**：1-2 個 session（三支結構幾乎相同）

---

### 階段 3：`recommend-city`（多輪對話）

**目標**：輪次控制 + 違規重試機制。

| 任務 | 驗收條件 |
|---|---|
| 輪次計算（= `messages` 中 user 訊息數） | 不信任客端自報 |
| 三種輪次指令注入（1~3 / 4 / ≥5） | 第 5 輪必定給城市 |
| `shown_cities` 禁令段注入 + 字面比對硬檢查 | 命中時自動重試一次（回饋放 prompt **最結尾**）|
| 換城上限（`shown_cities` 長度 >5 → 不打 LLM 回固定文案） | 省下 LLM 呼叫 |
| 收斂輪 chips 後端強制覆寫（三顆固定） | App 可用文字比對綁行為 |

**這階段會第一次真正感受到模型差異**——弱模型很容易在第 5 輪同時違反「必須給城市」和「不能給清單內城市」。第 6 章的調校方法主要就是為這裡準備的。

**工作量估**：1-2 個 session

---

### 階段 4：Normalize 層（TDD）+ `travel-guide`

**目標**：全案最核心的一層，用 TDD 寫。

| 任務 | 驗收條件 |
|---|---|
| **先寫測試**：normalize 的所有邊界情況 | 見下方清單 |
| `normalize.py` 實作 | 全部測試通過 |
| `POST /v1/plan/travel-guide` | 端到端可產出完整行程 |
| `orders[]` / `products[]` 條件注入段 | 沒帶時 prompt 與基礎版完全相同 |

**normalize 的測試清單（每一項都要有測試）**：

- [ ] 缺欄位 → 補預設值
- [ ] `type` 不在列舉 → 退回 `"spot"`
- [ ] `time` 格式錯（`"9:00"`、`"25:99"`、`null`）→ `None`
- [ ] `transport_mode` 不在四個值內 → `None`
- [ ] 非 `spot` 的 item → `lat`/`lng` **key 被移除**（不是 None）
- [ ] 非 `logistics` 的 item → `transport_mode` **key 被移除**
- [ ] `oid`/`prod_id` 不在白名單 → `None`
- [ ] `oid`/`prod_id` 給陣列（非 scalar）→ `None`，**不 crash**
- [ ] 同一 `oid` 出現在多天 → 只認第一次
- [ ] 同一 `prod_id` 出現在多天 → 只認第一次（與 oid 各自獨立計數）
- [ ] `booked_anchor` 依 `oid` 推導；**`prod_id` 不影響它**
- [ ] `name` 空字串 → `None`
- [ ] day 編號依規則指定

**工作量估**：2-3 個 session（normalize 的邊界情況很多，值得慢慢做）

---

### 階段 5：`travel-revise`

**目標**：複用 normalize 層，處理「完整對話 + 完整行程」的來回。

| 任務 | 驗收條件 |
|---|---|
| day 依陣列順序重編 1..N | LLM 不需要輸出 `day` 欄位 |
| `changed_days` 後端 diff（逐位置比對） | 不採信 LLM 自報 |
| 離題/失敗 → **原樣返回輸入行程** | `days` 不會是空的 |
| 白名單併集（行程既有 ∪ 本次帶入） | 不重帶 `orders`/`products` 也不會弄丟 id |
| 兩個條件注入段（oid 不可刪 / prod_id 非必要不刪） | 強度差異正確 |

**工作量估**：1-2 個 session

> **到這裡 Phase 2 七支全部完成。建議在此停一下，把服務實際串進一個測試 App 或 Postman collection 完整跑一遍**，確認整條流程可用再往下做 Phase 1。

---

### 階段 6：儲存層（KV + 物件儲存）

**目標**：為 Phase 1 準備基礎設施。

| 任務 | 驗收條件 |
|---|---|
| KV 抽象（`get`/`set` with TTL / `lock`） | SQLite 與 Redis 兩種實作可切換 |
| TTL 過期清理 | 過期資料讀不到 |
| 併發鎖（原子性） | 兩個並行請求只有一個拿到鎖 |
| gallery LIST（最新 100 筆 + TTL） | 超過 100 筆會裁切 |
| 物件儲存上傳 + 公開 URL | 上傳後 URL 可從外網存取 |

**工作量估**：1 個 session

---

### 階段 7：Phase 1 文字三支

| 任務 | 驗收條件 |
|---|---|
| `data/quiz_dimensions.json`（原 DCS 題庫）+ `ai_partner.json` | 檔案格式與原始 DCS 一致 |
| `POST /v1/companion/quiz` | 加權隨機選題（`1/2^出現次數`）+ LLM 改寫語氣；失敗回原始題目 |
| `identity_mapper.py` 八人格規則式判定 | **純函式，完整單元測試**（不經 LLM）|
| `POST /v1/companion/quiz-completions` | `travel_identity` 以 mapping 結果**覆寫** LLM 輸出；成功才快取 24h |
| `POST /v1/companion/self-introduction` | 失敗 fallback 固定文案 |

**移植重點**：`TravelIdentityMapper` 的判定邏輯要從 PHP 原始碼完整搬過來（象限 = 興趣 valence × 刺激程度，子類型 = 同行偏好／在地互動），這是純規則、沒有 LLM，測試要能覆蓋八種結果。

**工作量估**：2 個 session

---

### 階段 8：產圖（`share-image-v2`）★ 最高風險

**目標**：能產出可用的 hero + 裝飾素材。**先做最小可行版，不要一開始就追求原始品質**。

**建議分兩步走**：

**8a. 最小可行版**

| 任務 | 驗收條件 |
|---|---|
| Image provider 抽象 + 一家實作 | 能產出一張圖並上傳到物件儲存 |
| hero 產圖（**純文字 prompt，先不用參考圖**）| 產出無文字的目的地場景圖 |
| 三層 prompt 組合（art_style + 主體 + **硬約束**）| 硬約束確實生效（產出無文字） |
| 冪等（key 含 prompt SHA-256）| 同 uuid 重打不重產 |
| 併發鎖 | 併發請求只產一次 |
| `status` 只有 `ready`/`processing` | 沒有 `failed` |

**8b. 完整版（視 8a 結果決定要不要做）**

| 任務 | 前提 |
|---|---|
| stamp + tag×3 裝飾素材 | 需要能穩定產出指定背景色（深炭黑 / 純白） |
| 跨用戶共用快取（依 destination / tag） | 8a 完成 |
| 參考圖風格一致性 | **provider 要支援 image-to-image** |
| 旅伴人物合成 | **provider 要支援多圖輸入**；不支援就砍掉這個功能 |
| 逐素材 fallback 分類 | 8a 完成 |

**⚠️ 這階段最可能需要妥協**。優先順序：hero（核心）> 裝飾素材 > 風格一致性 > 旅伴合成。前兩項做出來就有 80% 價值。

**工作量估**：3-5 個 session（不確定性最高）

---

### 階段 9：收尾

| 任務 | 驗收條件 |
|---|---|
| `GET /v1/companion/quiz-gallery` | 只收錄產圖成功的項目 |
| 三支假資料端點（orders / wish_list / history） | 讀 `data/fake/*.json` 原樣回傳 |
| 全 17 支的 OpenAPI 文件（FastAPI 自動產生 + 補描述）| `/docs` 可讀 |
| Rate limiting（對應原始 throttle）| 各端點獨立計數 |
| 基本監控（錯誤率、LLM 失敗率、延遲）| 有地方看得到 |
| README（部署方式、環境變數、限制）| 半年後自己看得懂 |

**工作量估**：1-2 個 session

---

### 總估算

| 階段 | Session 數 |
|---|---|
| 0 選型驗證 | 1-2 |
| 1 LLM 層 + travel-summary | 1-2 |
| 2 三支城市判讀 | 1-2 |
| 3 recommend-city | 1-2 |
| 4 normalize + travel-guide | 2-3 |
| 5 travel-revise | 1-2 |
| 6 儲存層 | 1 |
| 7 Phase 1 文字三支 | 2 |
| 8 產圖 | 3-5 |
| 9 收尾 | 1-2 |
| **合計** | **14-23 個 session** |

> 這個估算**不含 prompt 調校的反覆時間**。實務上每個階段都會回頭調 prompt，建議在估算上再加 30-40%。

---

## 6. Prompt 移植與調校方法

### 6.1 移植步驟

1. **原樣複製**：先把 `ai-companion-complete-guide.md` 第 8 章的 prompt 全文原樣搬進 `prompts/` 檔案，一個字都不要改
2. **只改 placeholder 語法**（`{persona}` → Jinja2 的 `{{ persona }}`）
3. **先用原樣版本實測**，記錄失敗模式
4. **針對失敗模式調整**，一次只改一個地方

**不要一開始就「順便優化」prompt**——你會分不清是移植錯誤還是模型差異造成的問題。

### 6.2 換模型後最可能壞掉的四件事

| 症狀 | 診斷 | 對策 |
|---|---|---|
| 回應包在 ` ```json ` 裡，或前後有解說文字 | 模型不遵守「只輸出純 JSON」 | ① 用 provider 的 JSON mode / structured output ② `decode_llm_json()` 加強容錯 ③ prompt 結尾再強調一次 |
| 吐簡體中文 | 模型的中文預設是簡中 | 在 persona 段與輸出格式段**各強調一次**「繁體中文」；必要時後處理轉換 |
| 多條約束只遵守一部分 | 模型注意力不足 | ① 減少規則數量（合併相近規則）② 把最重要的規則移到**結尾** ③ 換更強的模型跑這支 |
| 位置索引錯位或編造 | 模型算不好陣列位置 | ① 在 user message 明示 `index` 欄位（原始做法）② 減少單次輸入筆數 ③ 後端防呆已經會擋，確認擋得住 |

### 6.3 建立 prompt 回歸測試（強烈建議）

調 prompt 最大的痛苦是「改 A 修好了，B 卻壞了」。建議建一組**用真實 LLM 跑的回歸測試**：

```python
# tests/prompt_regression/test_guide.py
# 這些測試會真的呼叫 LLM，所以標記起來、不進 CI，手動執行
import pytest

pytestmark = pytest.mark.prompt_regression

@pytest.mark.asyncio
async def test_guide_respects_city_and_day_count():
    result = await guide_service.generate({
        "summary": "想去京都放鬆三天",
        "city": "京都",
        "preferences": {"duration": "3天", "pace": "輕鬆"},
    })
    # 驗證「性質」而不是「精確文字」
    assert result["fail_reason"] is None
    assert result["city"] == "京都"
    assert len(result["itinerary_patch"]["days"]) == 3
    assert all(d["day"] == i + 1 for i, d in enumerate(result["itinerary_patch"]["days"]))

@pytest.mark.asyncio
async def test_guide_never_fabricates_prices():
    result = await guide_service.generate({...})
    text = json.dumps(result, ensure_ascii=False)
    # 商業資訊禁令
    assert not re.search(r"(NT\$|\d+\s*元|營業時間)", text)

@pytest.mark.asyncio
async def test_guide_schedules_all_required_products():
    products = [{"prod_id": "P1", "prod_name": "新天鵝堡之旅"}]
    result = await guide_service.generate({..., "products": products})
    all_ids = {it.get("prod_id") for d in result["itinerary_patch"]["days"] for it in d["items"]}
    assert "P1" in all_ids   # 「必須排入」是硬需求
```

**測「性質」不測「精確輸出」**——LLM 每次結果都不同，斷言精確字串必然失敗。要斷言的是：格式對不對、約束有沒有被違反、必要項目有沒有出現。

**執行時機**：每次調完 prompt 跑一次；換 provider 時全跑。因為會消耗免費額度，不要放進 CI 自動跑。

### 6.4 每支 API 的關鍵驗收（調 prompt 時對照）

| API | 一定要驗證的行為 |
|---|---|
| `quiz` | 改寫後 `id`/`index` 沒被改動；失敗時回原始題目 |
| `quiz-completions` | 推薦城市不在 `shown_cities`；`highlight_tags` 是從輸入原樣挑的 3 個；`travel_identity` 被 mapping 覆寫 |
| `travel-summary` A/A2 | 回應 `city` == 請求 `city`（100% 一致） |
| `from-orders` | `cities` 長度 == `orders` 長度；只回城市不回國家/行政區 |
| `from-wish` / `-history` | `product_indexes` 全在範圍內；同商品不掛兩城市；≤3 個城市 |
| `recommend-city` | 第 5 輪必給城市；給的城市不在 `shown_cities` |
| `travel-guide` | 天數符合 `duration`；`city` 一致；`orders`/`products` 全部排入；無捏造價格；`name`/`text` 分工正確 |
| `travel-revise` | 未變動的天內容與輸入一致；離題時原樣返回；`oid` 項目沒被刪 |

---

## 7. 開發工作流

### 7.1 用 SDD，spec 已經寫好了

**核心優勢：你已經有 spec。** `ai-companion-complete-guide.md` 包含全部 17 支的輸入輸出、prompt 全文、normalize 規則、設計理由。SDD 最貴的一步（寫規格）已經完成。

**每個 session 的標準流程**：

```
1. 開場：把兩份文件給 AI
   - ai-companion-complete-guide.md（原始規格）
   - 本計畫文件（進度與階段定義）
   並說明：「我要做階段 N，前面階段已完成」

2. AI 先確認理解：讀 spec 中對應章節，複述這階段要做什麼、驗收條件是什麼

3. 實作：
   - 純函式部分（normalize / mapper / prompt 組裝）→ 先寫測試（TDD）
   - I/O 部分（LLM 呼叫 / 儲存）→ 先寫 mock 測試，再實打驗證

4. 收尾（缺一不可）：
   - 部署上去
   - 用真實 LLM 打過
   - 對照驗收條件逐項確認
   - 更新本文件的進度表
```

### 7.2 切片原則：垂直切

```
❌ 水平切（做到一半什麼都跑不起來）
   所有 schema → 所有 service → 所有 route → 部署

✅ 垂直切（每個 session 結束都有能用的東西）
   一支 API 端到端（schema + service + route + test + 部署 + 實打）
```

這也是為什麼路線圖是「按 API 分階段」而不是「按層分階段」。

### 7.3 superpowers 的定位

superpowers 跟 SDD **不衝突，但層級不同**：

| | 管什麼 | 在這個專案怎麼用 |
|---|---|---|
| **SDD** | 跨 session 的連續性、規格與實作對齊 | **主軸**——每個階段就是一個 change |
| **superpowers** | 單一 session 內的工作品質 | **輔助**——特別是這三個 skill |

具體建議：

- **`test-driven-development`**：階段 4（normalize 層）**一定要用**。那層是純函式、邊界情況多，是 TDD 的完美標的
- **`systematic-debugging`**：階段 3 和 8 會需要（prompt 不聽話、產圖不如預期時，容易陷入亂試 prompt 的循環，需要系統性方法）
- **`verification-before-completion`**：每個階段收尾用——避免「我覺得寫完了」但沒實際部署驗證

**不建議用 `brainstorming`**——設計決策在原始服務裡都已經做過並記錄在 spec 裡，不需要重新發想。

### 7.4 進度追蹤

在本文件維護一張表，每個 session 結束更新：

| 階段 | 狀態 | 完成日 | 備註（實際踩到的問題） |
|---|---|---|---|
| 0 選型驗證 | ✅ 完成 | 2026-08-14 | 選型：Cloud Run（asia-east1，專案 ai-companion-505507）+ Neon + Gemini + Groq。SQLite 不可用 → 改 Neon。服務：https://ai-companion-api-30568057620.asia-east1.run.app 。全驗收通過：/health ✅、ai-partner 信封 ✅、400 契約 ✅、45s 請求存活 ✅（timeout 硬門檻）、**冷啟動 3.5s**（閒置 4hr 後實測；熱請求 0.05s，無需保溫）、**記憶體 RSS 58.8MB / 512Mi ≈ 11%** ✅、CI 自動部署 ✅。真實 ai_partner.json（v11）已上線，附贈 partner_intro_prompt（階段 7 可用）；120 張頭像壓縮（246MB→16.6MB, 512px）並搬到自有 GCS（ai-companion-assets-allenljf，公開讀取）。踩過的坑：① gh CLI 登錯帳號把 repo 建到公司帳號（已刪重建於 allenljf）② token 缺 workflow scope ③ API 啟用有傳播延遲 ④ **GCP_PROJECT_ID secret 誤填專案名稱而非 ID**（deploy 三連敗主因）⑤ GCS 公開讀取要先解除 public access prevention 再加 allUsers。觀測點：/debug/sleep、/debug/memory |
| 1 LLM 層 + travel-summary | ✅ 完成 | 2026-08-14 | 全驗收通過：LLMProvider 抽象（Gemini/Groq 共用 OpenAICompatProvider，走 OpenAI 相容端點）、decode_llm_json（容忍 fence + 框外解說）、call_and_parse 軟失敗、Jinja2 prompt 檔、`POST /v1/plan/travel-summary` 四入口線上實打 ✅、400 契約 ✅。**踩到的坑**：① `gemini-2.5-flash` 對新用戶已停用（404 "no longer available to new users"），且 **Gemini prepay credits 已耗盡（429）**→ 改用 Groq `qwen/qwen3.6-27b`（繁中品質好、支援 image 輸入可覆蓋 B 入口 vision、json_mode）② **Qwen 是 reasoning 模型，思考 tokens 會吃光 max_tokens 導致 json_validate_failed 空回應**→ 抽象層加 reasoning_effort 支援，路由格式 `provider:model[:reasoning_effort]`（env `LLM_TRAVEL_SUMMARY`，預設 `groq:qwen/qwen3.6-27b:none`）③ CI 補 `--update-env-vars` 送金鑰上 Cloud Run（GitHub secrets：GEMINI_API_KEY/GROQ_API_KEY）。**觀察**：A2 開場白偶有輕微幻覺（提到未輸入的「住宿」）、長度偶爾超 120 字——記入 prompt 調校待辦，未擋驗收。prompt 回歸測試 3 條已建立（`pytest -m prompt_regression`）。 |
| 2 三支城市判讀 | ✅ 完成 | 2026-08-14 | 全驗收通過：三支 API 部署 + 線上實打 ✅。位置索引防呆完整測試（超範圍/非數字/重複索引/跨城重複掛商品/大小寫去重/上限 3），「LLM 回超範圍 index → 不 crash 不產假 prod_id」有 unit + integration 雙層測試。wish/history 依 spec 刻意獨立實作；persona 組裝抽成共用 `services/plan/persona.py`（跨 Phase 慣例，非判讀邏輯）。模型沿用 `groq:qwen/qwen3.6-27b:none`，三支各自可用 env 換模型。**踩到的坑**：`render_prompt(name=...)` 與模板變數 `name` 撞名 → 第一參數改 positional-only。**實測品質**：國家/行政區收斂正確（美國→奧蘭多、北海道→小樽、新天鵝堡→慕尼黑合併）；**觀察**：from-history 在單一城市情境下 greeting 會提到城市名（違反規則 6「不可條列城市名稱」）——記入 prompt 調校待辦。 |
| 3 recommend-city | ✅ 完成 | 2026-08-15 | 全驗收通過：輪次伺服器計算、三種輪次指令（單一 Jinja2 模板 + 條件段）、shown_cities 禁令 + 字面硬檢查 + 違規自動重試（回饋在 prompt 最結尾）、換城 >5 不打 LLM、收斂 chips 後端強制覆寫（滿 5 個不遞「換一個城市」）。線上實測：第 5 輪必給城市且避開禁令、離題正確導回 + off_topic=true、400 契約 ✅。**風險 #3 應驗（本階段最大收穫）**：qwen3.6 在對話型輸出會吐簡中——① prompt 輸出格式段（結尾）補強繁中要求（改善大段簡中）② 單字級滲漏（「预算」）壓不死 → 加 **OpenCC s2twp 後處理**（`app/core/zh.py`），且 recommended_city **轉繁後才做違規比對**（防簡體拼寫繞過禁令）。目前只套在 recommend-city，**待辦：回頭掃 travel-summary 等其他端點的輸出也套 to_traditional**。**額度教訓**：Groq 免費層此模型 **TPM 8000**（每日 1000 req），recommend-city prompt 長、一分鐘只夠 4~5 次呼叫——回歸測試連跑會 429（軟失敗正確回 fail_reason）；跑 prompt_regression 要間隔 60-90 秒。回歸測試新增簡體字滲漏偵測 helper。 |
| 4 normalize + travel-guide | ✅ 完成 | 2026-08-15 | 全驗收通過：normalize 層 TDD（44 條測試，逐條對照 ItineraryDayNormalizeTrait，含白名單/去重/booked_anchor/條件性 key 移除/非 scalar 防炸）、truncate_at_sentence、`POST /v1/plan/travel-guide` 部署 + 線上實打 ✅（含 orders/products 情境）、條件注入段「沒帶時與基礎版完全相同」有測試。與 PHP 兩個**刻意差異**：① 未知欄位丟棄（PHP 會透傳）② lat/lng 非數值 → None（PHP (float) cast 會變 0.0 null island）。**踩到的坑**：① qwen 的 **TPM 8000 裝不下 guide 長 prompt + max_tokens 8000**（實測 413 Payload Too Large）→ travel_guide 路由改 `groq:llama-3.3-70b-versatile`（TPM 12000、非 reasoning 模型，繁中品質靠 OpenCC 後處理補）② llama-3.3-70b 會把最後一天**拆成兩個 day=5 區塊** → 加後端防呆：模型 day 編號不唯一時改用陣列索引重編（與 PHP 刻意不同）。**觀察（prompt 調校待辦）**：a) products「必須排入」偶爾失守（實打兩次一次沒回填 prod_id；摘要有提到該商品時較穩）b) meal 的 name 偶爾留空（違規則 5）c) 空 items 的天偶爾不標 unplanned（違規則 2「誠實優於填充」）d) date_range 未依 go_dt 推導、pending_fields 未加「航班」（規則 3 部分遵守）。簡轉繁後處理已套 guide 全輸出欄位；**travel-summary 系列回頭掃 to_traditional 仍待辦**。prompt 回歸測試 4 條已建（真實 LLM 全過；連跑注意 TPM，間隔 60-90s）。 |
| 5 travel-revise | ✅ 完成 | 2026-08-15 | 全驗收通過：`POST /v1/plan/travel-revise` 部署 + 線上實打 ✅。day 依陣列順序重編 1..N（忽略輸入/模型 day 欄位）、changed_days 後端逐位置 diff、離題/失敗一律原樣返回**正規化過的輸入行程**（離題 fail_reason=null 照常計費）、白名單併集（行程既有 ∪ 本次帶入，oid/prod_id 各自獨立、防非 scalar）、兩個條件注入段依**白名單有值**注入（不是 request 有沒有帶 orders）且強度差異正確。guide 的材料組裝函式（payload/白名單/文字轉繁）抽公用給 revise 共用。回歸測試 4 條真實 LLM 全過：未變動天原樣保留、**拒刪已預訂 oid 項目 ✅**、離題原樣返回、刪一天重編。線上實打：修改情境（只動 Day 1、oid 保留、diff=[1]）與離題情境都正確。**注意**：diff 只迭代 revised 位置，「刪掉最後一天」changed_days 會是空（對照 PHP 行為，UI 提示用途可接受，有測試記錄）。**觀察（prompt 調校待辦）**：沒帶 persona 時離題導回文案偏生硬（「離題了」3 個字）；itinerary 30 天 ×50 items 上限下輸入可能逼近 llama TPM 12000，大行程 + 長對話會 413/429——目前實測 2 天行程 + 3 則對話約佔 3-4k tokens 無虞，大行程情境待 App 端實測。**Phase 2 七支全部完成**——依計畫建議可做一次「假發表」完整跑通選城市→排行程→改行程。 |
| 6 儲存層 | ✅ 完成 | 2026-08-15 | 全驗收通過（`/debug/storage` 線上實打全綠）：KV 抽象 async 化（InMemory / **Neon Postgres** 兩實作，deps 依 DATABASE_URL 切換）、TTL 過期讀不到 ✅、**併發鎖原子性**（`add()` 單一 `INSERT ... ON CONFLICT ... WHERE expired RETURNING` 語句，10 路並行只有 1 個拿到，本地打真實 Neon + 線上都驗過）✅、gallery LIST（裁切 + 1 週 TTL，push 時順帶清理）✅、GCS 上傳 + 公開 URL 外網可讀 ✅（runtime SA 已授 bucket objectAdmin）。quiz-completions 快取已切到 Neon：**線上寫入、本地讀到，跨機器共用確認**——階段 8 share-image 的前置依賴解除。**踩到的坑**：CI `--update-env-vars` 用 `^@^` 當分隔符，但 DATABASE_URL 含 `@`（user:pass@host）被切成兩個 env → 連線 PoolTimeout 30s；換 `^##^` 分隔符並清掉被切出的垃圾 env。**觀察**：completions 又出現 destination_cn 給英文（"Queenstown"）——連同「回國家非城市」一起記入 prompt 調校待辦。整合測試需 DATABASE_URL（CI 無會 skip）；GCS 測試本地無 ADC 會 skip，靠 `/debug/storage` 線上驗。 |
| 7 Phase 1 文字三支 | ✅ 完成 | 2026-08-15 | 全驗收通過：三支部署 + 線上實打 ✅。`TravelIdentityMapper` 純函式移植（24 條測試：八人格、1:1 平手以 stimulation 破、neutral 題序優先、novel-leaning 借力、company>local、輔助標籤不參與）。quiz 加權隨機選題（1/2^出現次數，注入 rng 可測）+ LLM 只改寫語氣（失敗 fallback 原題）；completions `travel_identity` 一律 mapping 覆寫、quote ≤30/recommendation ≤200 截斷、**成功才快取 24h**；self-introduction 用 `partner_intro_prompt`（ai_partner v11 內附）、純文字輸出、fallback 固定文案。題庫 149 選項圖已上自有 GCS（`quiz/` 路徑，2026-08-15 已從筆誤的 quzi/ 改名完成）。**快取為記憶體 KV 過渡版**（`app/storage/kv.py`，介面對齊階段 6）：Cloud Run 重啟即遺失、多副本不共用——**階段 8 的 share-image 依賴此快取讀回分析，開工前必須先做階段 6 換 Neon**。模型：quiz/self-intro 走 qwen:none（輕量）、completions 走 llama-3.3-70b（重度結構化）。**觀察（prompt 調校待辦）**：① completions 目的地偶爾回**國家**（實打得到「喀麥隆」）而非城市——「具體城市（非國家、非區域）」規則失守 ② reasoning 9 句 <10、recommendation 每段 33-53 字遠低於 100-140 要求（llama 對字數規格遵循弱）③ qwen 改寫偶有贅字（「藏好久之的」）。 |
| 8 產圖 | ✅ 8a 完成（8b 調校待辦） | 2026-08-16 | **8a 全驗收通過**（線上實打）：provider 抽象 + Cloudflare Workers AI 實作（選型調研見 `docs/research/image-gen-free-tier.md`）、hero 純文字 prompt 產無文字直式場景圖（klein-4b multipart、1152×2048、**首打 5 張 27.7s**——v2 <30s 目標達成）、三層 prompt（art_style + 主體 + 硬約束寫死）、冪等（key 含 hero prompt SHA-256，重打 0.65s 命中不重產）、completion/pack 雙層鎖（unit 併發測試 + 線上 processing 行為）、status 只有 ready/processing、C007 契約 ✅、hero 成功寫 gallery（Neon 驗證）✅。**Spike 關鍵結論**：klein-4b 只吃 multipart form（JSON 400）；**參考圖欄位被靜默忽略**（image/images/input_image 都回 200 但輸出無風格影響）→ 依 3.3 降級 ①③：hero 純文字、旅伴合成砍掉（partner_image_url 收下忽略）；產出是 JPEG 非 PNG。用量試算：hero 313 + 裝飾 4×57.6 ≈ 543 neurons/次 → 免費層約 18 次測驗/天。**8b 調校待辦**：① stamp/tag 硬約束在 schnell 上失守（stamp 渲染可讀 "NEW YORK"、背景非深炭黑；tag 變整張場景圖非白底置中）——方向：精簡 schnell prompt（蒸餾模型對長 prompt 遵循差）或裝飾改 klein（成本 ×5.4，額度剩 6 次/天）② hero 背景霓虹有偽字形（可接受但可再壓）③ 跨用戶裝飾快取已實作待自然驗證。 |
| 9 收尾 | ⬜ | | |

**「備註」欄很重要**——記錄實際踩到的坑（哪個模型不聽話、哪個平台限制、prompt 怎麼調才通），這些是下次開新對話時最有價值的上下文。

---

## 8. 風險清單與 fallback

| # | 風險 | 機率 | 影響 | Fallback |
|---|---|---|---|---|
| 1 | **免費 hosting timeout <60s** | 中 | 致命（`travel-guide` 無法運作） | 階段 0 就驗證；不通立刻換平台。最後手段：改成非同步任務 + 輪詢（大改，避免） |
| 2 | **免費 LLM 的 JSON 遵循度差** | **高** | 高（大量 `llm_error`） | ① 用 provider 的 JSON mode ② 加強 `decode_llm_json` 容錯 ③ 重試一次 ④ 重度端點換更強模型 |
| 3 | **繁中變簡中** | 中 | 中 | prompt 多處強調 + 後處理轉換（OpenCC） |
| 4 | **`travel-guide` 11 條規則遵守不完全** | **高** | 中（行程品質下降但可用） | 精簡規則數；把關鍵規則移到結尾；接受品質落差 |
| 5 | **圖片生成不支援參考圖** | 中 | 中（風格不一致） | 純文字 prompt + 強化風格描述；砍掉旅伴人物合成 |
| 6 | **圖片模型產出含文字/亂碼** | 中 | 中 | 硬約束已在 prompt；若仍發生，換模型或加後處理偵測 |
| 7 | **冷啟動太久（>30s）** | 中 | 中（首次體驗差） | 定時 ping 保溫（算進流量額度）；或選常駐方案 |
| 8 | **免費額度用完** | 中 | 中 | 多 provider 輪替；加自己的 rate limit；重要展示前先確認額度 |
| 9 | **SQLite 在多副本/暫時檔案系統下失效** | 中 | 中 | 階段 0 驗證；不行就換外部 KV |
| 10 | **做到一半失去動力** | **高** | 致命 | **這是最大的風險**。緩解：垂直切片（每階段都有可用成果）；Phase 2 完成（階段 5）就已經是一個完整可用的服務，可以先發表/展示再繼續 |

### 關於風險 10 的具體建議

路線圖刻意設計成**階段 5 結束時就是一個完整可用的產品**（Phase 2 七支全通，能從「選城市」到「排行程」到「改行程」）。

**建議在階段 5 結束時做一次「假發表」**——寫個簡單的前端或 Postman collection，完整跑一遍展示給自己看。這個成就感是撐過階段 8（產圖）的燃料。

---

## 附錄：開新對話時的起手式

在**這個專案資料夾**（`ai-companion-api/`）開新對話。`CLAUDE.md` 會被自動載入，
所以不需要重述技術決策與工作流程，只要說明「這次要做哪個階段」：

```
我要做【階段 N：XXX】。

請先讀 docs/migration-plan.md 的進度表確認前面階段的狀況，
再讀 docs/source-spec.md 的對應章節，
然後複述：這階段要做什麼、驗收條件有哪些，確認理解一致再開始實作。
```

**如果是接續上次未完成的階段**，補一句：

```
上次做到一半，卡在 XXX（或：已完成 A、B，還差 C）。
```

**需要查原始 PHP 實作細節時**（例如 prompt 完整原文、normalize 的邊界處理）：

```
請看 reference/services/TravelGuideService.php 的 buildSystemPrompt()，
確認 prompt 原文與 docs/source-spec.md 第 8.10 節是否一致。
```

`reference/README.md` 有「哪支 API 看哪個檔案」的完整索引。
