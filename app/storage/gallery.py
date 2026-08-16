"""gallery 的記憶體實作（本地開發/測試；正式環境用 app/storage/pg.PostgresGallery）。"""


class InMemoryGallery:
    def __init__(self, *, limit: int = 100):
        self.limit = limit
        self._records: list[dict] = []

    async def push(self, record: dict) -> None:
        # 不裁切（同 Postgres 版：資料保留，「最新 limit 筆」由讀取端實現）
        self._records.insert(0, record)

    async def list(self) -> list[dict]:
        return list(self._records[: self.limit])
