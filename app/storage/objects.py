"""物件儲存（GCS）：圖片上傳 + 公開 URL（階段 6；階段 8 產圖與 quiz-gallery 用）。

- bucket 為 uniform bucket-level access + allUsers 公開讀取（階段 0 已設），
  上傳後即可用 storage.googleapis.com 公開 URL 存取，不需逐物件 ACL
- 憑證走 ADC：Cloud Run 上是 runtime service account，本地是
  `gcloud auth application-default login`
- 同步 client（google-cloud-storage 無官方 async）；量小、單張圖上傳 <1s，
  async 呼叫端需要時用 anyio.to_thread 包
"""

from functools import lru_cache

from google.cloud import storage

BUCKET_NAME = "ai-companion-assets-allenljf"
PUBLIC_BASE = f"https://storage.googleapis.com/{BUCKET_NAME}"


@lru_cache(maxsize=1)
def _bucket() -> storage.Bucket:
    return storage.Client().bucket(BUCKET_NAME)


def upload_object(key: str, data: bytes, *, content_type: str) -> str:
    """上傳並回公開 URL。同 key 重傳直接覆蓋（冪等靠呼叫端把版本因子放進 key）。"""
    blob = _bucket().blob(key)
    blob.upload_from_string(data, content_type=content_type)
    return f"{PUBLIC_BASE}/{key}"


def object_exists(key: str) -> bool:
    """產圖冪等檢查用：同 key 已存在就不重產。"""
    return _bucket().blob(key).exists()


def public_url(key: str) -> str:
    return f"{PUBLIC_BASE}/{key}"
