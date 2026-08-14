from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ai_partner_envelope_and_passthrough():
    res = client.get("/v1/companion/ai-partner")
    assert res.status_code == 200
    body = res.json()
    assert body["metadata"] == {"status": "0000", "desc": "Success"}
    # 原樣透傳 data/ai_partner.json（原始服務回傳的 DCS 物件：variant 內含選項）
    variant = body["data"]["variant"]
    for key in ("personality", "speech_style", "gender", "avatars", "hair_style", "hair_color", "outfit"):
        assert key in variant
    assert len(variant["personality"]) == 11
    assert all({"tag", "label", "description"} <= p.keys() for p in variant["personality"])
    assert len(variant["avatars"]) == 120


def test_debug_sleep_returns_envelope():
    res = client.get("/debug/sleep", params={"seconds": 0})
    assert res.status_code == 200
    body = res.json()
    assert body["metadata"]["status"] == "0000"
    assert body["data"]["requested_seconds"] == 0


def test_validation_error_is_400_without_data_field():
    # seconds 給非數字 → 驗證失敗必須回 400（不是 500），且信封沒有 data 欄位
    res = client.get("/debug/sleep", params={"seconds": "abc"})
    assert res.status_code == 400
    body = res.json()
    assert body["metadata"]["status"] == "110001"
    assert isinstance(body["metadata"]["desc"], list) and body["metadata"]["desc"]
    assert "data" not in body
