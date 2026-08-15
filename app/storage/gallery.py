"""gallery 的記憶體實作（本地開發/測試；正式環境用 app/storage/pg.PostgresGallery）。"""


class InMemoryGallery:
    def __init__(self, *, limit: int = 100):
        self.limit = limit
        self._records: list[dict] = []

    async def push(self, record: dict) -> None:
        self._records.insert(0, record)
        del self._records[self.limit :]

    async def list(self) -> list[dict]:
        return list(self._records)
