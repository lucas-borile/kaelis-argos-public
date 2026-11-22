from __future__ import annotations

from datetime import datetime, timezone


def create_temporal_event() -> dict:
    import db

    now = datetime.now(timezone.utc).isoformat()
    return db.save_temporal_event(
        {
            "event_type": "temporal_geofence_alert",
            "rule_id": 1,
            "track_id": "cam:pessoa:1",
            "camera_id": "CAM_TEST",
            "geofence_id": 1,
            "object_type": "pessoa",
            "severity": "high",
            "message": "Pessoa parada em area sensivel",
            "status": "open",
            "seen_duration_seconds": 240,
            "geofence_duration_seconds": 240,
            "stationary_duration_seconds": 180,
            "created_at": now,
            "first_evidence_at": now,
            "last_evidence_at": now,
            "frame_reference": None,
        }
    )


def test_attention_queue_lists_open_temporal_events(admin_client):
    event = create_temporal_event()

    response = admin_client.get("/api/attention")

    assert response.status_code == 200, response.text
    payload = response.json()
    keys = {item["item_key"] for item in payload["items"]}
    assert f"temporal_event:{event['id']}" in keys
    item = next(row for row in payload["items"] if row["item_key"] == f"temporal_event:{event['id']}")
    assert item["severity"] == "high"
    assert item["status"] == "open"
    assert item["score"] >= 70


def test_operator_can_assign_and_resolve_attention_item(admin_client):
    event = create_temporal_event()
    item_key = f"temporal_event:{event['id']}"

    assigned = admin_client.patch(
        f"/api/attention/{item_key}",
        json={"status": "assigned", "comment": "em atendimento"},
    )

    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "assigned"
    assert assigned.json()["assigned_to"] == "admin"

    resolved = admin_client.patch(
        f"/api/attention/{item_key}",
        json={"status": "resolved", "resolution": "sem risco"},
    )

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolution"] == "sem risco"

    queue = admin_client.get("/api/attention")
    keys = {item["item_key"] for item in queue.json()["items"]}
    assert item_key not in keys

    with_resolved = admin_client.get("/api/attention?include_resolved=true")
    keys = {item["item_key"] for item in with_resolved.json()["items"]}
    assert item_key in keys


def test_quick_confirm_risk_assigns_attention_item(admin_client):
    event = create_temporal_event()
    item_key = f"temporal_event:{event['id']}"

    response = admin_client.post(
        f"/api/attention/{item_key}/confirm",
        json={"action": "confirm_risk", "comment": "risco validado no frame"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "confirm_risk"
    assert payload["state"]["status"] == "assigned"
    assert payload["state"]["assigned_to"] == "admin"
    assert payload["state"]["quick_action"] == "confirm_risk"
    assert payload["state"]["confirmed_at"]


def test_quick_false_alarm_resolves_and_hides_from_default_queue(admin_client):
    event = create_temporal_event()
    item_key = f"temporal_event:{event['id']}"

    response = admin_client.post(
        f"/api/attention/{item_key}/confirm",
        json={"action": "false_alarm"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"]["status"] == "resolved"
    assert response.json()["state"]["resolution"] == "falso alarme"

    queue = admin_client.get("/api/attention")
    keys = {item["item_key"] for item in queue.json()["items"]}
    assert item_key not in keys
