"""quiz-gallery 與三支假資料端點（階段 9 驗收）。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_gallery
from app.main import app
from app.storage.gallery import InMemoryGallery


@pytest.fixture
def gallery() -> InMemoryGallery:
    return InMemoryGallery()


@pytest.fixture
def client(gallery) -> TestClient:
    app.dependency_overrides[get_gallery] = lambda: gallery
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestQuizGallery:
    def test_empty_gallery(self, client):
        res = client.get("/v1/companion/quiz-gallery")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data == {"count": 0, "items": []}

    def test_lists_records_newest_first(self, client, gallery):
        for i in range(2):
            asyncio.run(
                gallery.push(
                    {
                        "travel_identity": f"人格{i}",
                        "share_image_url": f"https://x/{i}.jpg",
                        "companion_name": "阿旅",
                    }
                )
            )
        data = client.get("/v1/companion/quiz-gallery").json()["data"]
        assert data["count"] == 2
        assert data["items"][0]["travel_identity"] == "人格1"

    def test_filters_records_without_image(self, client, gallery):
        # 防禦性過濾：只收錄產圖成功的項目（share_image_url 為空的不回）
        asyncio.run(gallery.push({"travel_identity": "無圖", "share_image_url": ""}))
        asyncio.run(gallery.push({"travel_identity": "有圖", "share_image_url": "https://x/1.jpg"}))
        data = client.get("/v1/companion/quiz-gallery").json()["data"]
        assert data["count"] == 1
        assert data["items"][0]["travel_identity"] == "有圖"


class TestFakeDataEndpoints:
    @pytest.mark.parametrize("path,expect_in_data", [
        ("/v1/companion/orders", "orders"),
        ("/v1/companion/wish_list", "prods"),
        ("/v1/companion/history", "prods"),
    ])
    def test_returns_file_verbatim_with_envelope(self, client, path, expect_in_data):
        res = client.get(path)
        assert res.status_code == 200
        body = res.json()
        # 原樣回傳：檔案本身就含 metadata/dynamic/queue_it 信封，不再包一層
        assert body["metadata"]["status"] == "0000"
        assert "dynamic" in body and "queue_it" in body
        assert expect_in_data in body["data"]
