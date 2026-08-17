"""POST /v1/companion/share-image-v2 端到端（fake provider）：400 / C007 / ready 契約。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_gallery, get_image_provider, get_kv, get_object_store
from app.main import app
from app.services.companion.completion import CACHE_KEY_PREFIX
from app.storage.gallery import InMemoryGallery
from app.storage.kv import InMemoryKV
from app.storage.objects import InMemoryObjectStore

UUID = "b0e7a3a8-8f2f-4c1e-9d2f-1c9a35c1deee"
URL = "/v1/companion/share-image-v2"
JPEG = b"\xff\xd8\xff\xe0" + b"img"


class FakeProvider:
    async def generate(self, prompt, *, model, width=None, height=None, steps=None):
        return JPEG


@pytest.fixture
def kv() -> InMemoryKV:
    return InMemoryKV()


@pytest.fixture
def client(kv) -> TestClient:
    app.dependency_overrides[get_kv] = lambda: kv
    app.dependency_overrides[get_image_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_gallery] = lambda: InMemoryGallery()
    app.dependency_overrides[get_object_store] = lambda: InMemoryObjectStore()
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed_analysis(kv):
    asyncio.run(
        kv.set(
            CACHE_KEY_PREFIX + UUID,
            {
                "analysis": {
                    "travel_identity": "獨處療癒師",
                    "travel_identity_en": "Solo Healer",
                    "destination_cn": "京都",
                    "destination_en": "Kyoto",
                    "tagline": "慢慢走",
                    "highlight_tags": ["身心療癒派", "大自然充電族", "安心舒適圈"],
                    "companion_quote": "慢慢走。",
                },
                "companion_name": "阿旅",
                "partner_avatar_url": None,
            },
            ttl_seconds=3600,
        )
    )


def test_invalid_uuid_400(client):
    res = client.post(URL, json={"completion_uuid": "nope"})
    assert res.status_code == 400
    assert res.json()["metadata"]["status"] == "110001"


def test_missing_analysis_returns_c007(client):
    res = client.post(URL, json={"completion_uuid": UUID})
    assert res.status_code == 200
    assert res.json()["metadata"]["status"] == "C007"


def test_ready_flow(client, kv):
    seed_analysis(kv)
    res = client.post(URL, json={"completion_uuid": UUID})
    assert res.status_code == 200
    body = res.json()
    assert body["metadata"]["status"] == "0000"
    data = body["data"]
    assert data["status"] == "ready"
    assert data["hero_url"].startswith("https://storage.googleapis.com/")
    assert data["decorations"]["stamp_url"] is None  # stamp 暫停用（見 share_image.GENERATE_STAMP）
    assert data["decorations"]["tag_icon_urls"] == []  # tag icon 暫停用（見 share_image.GENERATE_TAG_ICONS）
    assert data["content"]["travel_identity"] == "獨處療癒師"
    assert data["share_fallback"] is None and data["fail_reason"] is None


def test_partner_image_url_accepted_and_ignored(client, kv):
    seed_analysis(kv)
    res = client.post(
        URL, json={"completion_uuid": UUID, "partner_image_url": "https://x/avatar.png"}
    )
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "ready"
