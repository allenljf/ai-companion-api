"""GCS 物件上傳整合測試（需 Google 憑證，無則跳過；Cloud Run 上以 /debug/storage 實打驗收）。

驗收（migration-plan 階段 6）：上傳後公開 URL 可從外網存取。
"""

import urllib.request
import uuid

import pytest


def _has_google_credentials() -> bool:
    try:
        import google.auth

        google.auth.default()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_google_credentials(), reason="Google ADC not available"
)


def test_upload_then_public_url_readable():
    from app.storage.objects import object_exists, upload_object

    key = f"debug/test-{uuid.uuid4().hex}.txt"
    url = upload_object(key, b"storage smoke test", content_type="text/plain")

    assert url == f"https://storage.googleapis.com/ai-companion-assets-allenljf/{key}"
    assert object_exists(key) is True

    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200
        assert response.read() == b"storage smoke test"


def test_object_exists_false_for_missing():
    from app.storage.objects import object_exists

    assert object_exists(f"debug/nope-{uuid.uuid4().hex}") is False
