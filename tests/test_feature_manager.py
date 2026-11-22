from __future__ import annotations


def test_admin_can_list_feature_catalog(admin_client):
    response = admin_client.get("/api/admin/features")

    assert response.status_code == 200
    features = response.json()["features"]
    keys = {item["feature_key"] for item in features}
    assert "chat_enabled" in keys
    assert "telegram_enabled" in keys
    chat = next(item for item in features if item["feature_key"] == "chat_enabled")
    assert chat["enabled"] is True
    assert chat["disable_consequence"]
    assert chat["estimated_processing_impact"]
    assert chat["cpu_cost"]


def test_operator_cannot_update_feature_flags(app_client):
    login = app_client.post("/auth/login", json={"username": "operator", "password": "operator123"})
    assert login.status_code == 200

    response = app_client.patch("/api/admin/features/chat_enabled", json={"enabled": False})

    assert response.status_code == 403


def test_disabling_chat_blocks_chat_endpoints(admin_client):
    update = admin_client.patch(
        "/api/admin/features/chat_enabled",
        json={"enabled": False, "reason": "teste automatizado"},
    )
    assert update.status_code == 200
    assert update.json()["enabled"] is False

    response = admin_client.get("/chat/messages")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["feature_key"] == "chat_enabled"
    assert "Chat" in detail["feature_name"]


def test_feature_audit_log_records_changes(admin_client):
    update = admin_client.patch(
        "/api/admin/features/heatmap_enabled",
        json={"enabled": False, "reason": "reduzir painel"},
    )
    assert update.status_code == 200

    audit = admin_client.get("/api/admin/features/audit-log?feature_key=heatmap_enabled")

    assert audit.status_code == 200
    rows = audit.json()
    assert rows
    assert rows[0]["feature_key"] == "heatmap_enabled"
    assert rows[0]["new_enabled"] is False
    assert rows[0]["reason"] == "reduzir painel"
