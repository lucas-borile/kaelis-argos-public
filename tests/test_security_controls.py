from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers


def test_private_network_urls_are_blocked_by_default(monkeypatch):
    import network_security

    monkeypatch.setenv("ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS", "false")

    with pytest.raises(network_security.UnsafeNetworkTargetError):
        network_security.validate_safe_url("http://127.0.0.1:8000/internal")


@pytest.mark.parametrize(
    ("payload", "expected_detail"),
    [
        (b"", "empty upload"),
        (b"not an image", "floor plan image must be PNG or JPG"),
    ],
)
def test_floor_plan_upload_reader_rejects_invalid_payloads(payload, expected_detail):
    import main

    class FakeUpload:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.sent = False

        async def read(self, size: int = -1) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return self.data

    async def exercise():
        data = await main.read_limited_upload(FakeUpload(payload), 512)
        main.detect_floor_plan_image_type(data)

    with pytest.raises(HTTPException) as exc_info:
        import anyio

        anyio.run(exercise)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == expected_detail


def test_floor_plan_upload_reader_enforces_size_limit():
    import anyio
    import main

    class FakeUpload:
        def __init__(self) -> None:
            self.chunks = [b"a" * 600, b""]

        async def read(self, size: int = -1) -> bytes:
            return self.chunks.pop(0)

    async def exercise():
        await main.read_limited_upload(FakeUpload(), 512)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(exercise)

    assert exc_info.value.status_code == 413


def test_cors_allows_configured_origin(app_client):
    response = app_client.options(
        "/health",
        headers=Headers(
            {
                "origin": "http://127.0.0.1:8080",
                "access-control-request-method": "GET",
            }
        ),
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
