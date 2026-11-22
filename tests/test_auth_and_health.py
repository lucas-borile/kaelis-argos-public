from __future__ import annotations


def test_health_is_public_and_minimal(app_client):
    response = app_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_system_status_requires_authentication(app_client):
    response = app_client.get("/system/status")

    assert response.status_code == 401


def test_camera_health_requires_authentication(app_client):
    response = app_client.get("/api/cameras/health")

    assert response.status_code == 401


def test_camera_health_empty_summary(admin_client):
    response = admin_client.get("/api/cameras/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 0
    assert payload["items"] == []


def test_camera_health_flags_inactive_camera(admin_client):
    import main

    main.CAMERAS[:] = [
        {
            "id": "CAM_OFF",
            "name": "Portao pausado",
            "active": False,
            "lat": -23.5,
            "lon": -46.6,
            "priority_level": 6,
        }
    ]

    response = admin_client.get("/api/cameras/health?hours=24")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["camera_id"] == "CAM_OFF"
    assert item["status"] == "inactive"
    assert item["severity"] in {"medium", "high", "critical"}
    assert item["risk_score"] >= 45
    assert any(reason["kind"] == "inactive" for reason in item["reasons"])


def test_login_sets_http_only_session_cookie(app_client):
    response = app_client.post("/auth/login", json={"username": "admin", "password": "admin123"})

    assert response.status_code == 200
    assert "token" in response.json()
    cookie = response.headers.get("set-cookie", "")
    assert "argos_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    me_response = app_client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"


def test_login_rate_limit_blocks_repeated_failures(app_client):
    for _ in range(5):
        response = app_client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert response.status_code == 401

    blocked = app_client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
