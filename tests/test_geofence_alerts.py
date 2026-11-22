from __future__ import annotations


def sample_geofence_alert() -> dict:
    return {
        "event": "geofence_alert",
        "timestamp": "2026-06-28T12:00:00+00:00",
        "geofence_id": 1,
        "geofence_name": "Portao lateral",
        "geofence_priority": 7,
        "rule_id": 2,
        "rule_name": "Pessoa em area restrita",
        "object_type": "pessoa",
        "count": 1,
        "detection": {
            "camera_id": "CAM_TEST",
            "camera_name": "Camera teste",
            "object_type": "pessoa",
            "track_id": "track-1",
            "timestamp": "2026-06-28T12:00:00+00:00",
            "geo": {"lat": -23.5, "lon": -46.6, "accuracy_m": 5},
            "frame_id": 123,
        },
    }


def test_geofence_alert_resolution_persists_for_legacy_alert_without_incident(admin_client):
    import db

    alert_id = db.save_geofence_alert(sample_geofence_alert())

    listed = admin_client.get("/geofence-alerts")
    assert listed.status_code == 200, listed.text
    assert alert_id in {item["id"] for item in listed.json()}

    resolved = admin_client.patch(
        f"/geofence-alerts/{alert_id}",
        json={"status": "resolved", "resolution": "alerta antigo verificado"},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"]
    assert body["resolved_by"] == "admin"
    assert body["resolution"] == "alerta antigo verificado"
    assert body["incident_id"] is None

    default_list = admin_client.get("/geofence-alerts")
    assert alert_id not in {item["id"] for item in default_list.json()}

    with_resolved = admin_client.get("/geofence-alerts?include_resolved=true")
    resolved_item = next(item for item in with_resolved.json() if item["id"] == alert_id)
    assert resolved_item["status"] == "resolved"
    assert resolved_item["resolution"] == "alerta antigo verificado"


def test_geofence_alert_resolution_closes_linked_incident(admin_client):
    import db

    alert_id = db.save_geofence_alert(sample_geofence_alert())
    incident = admin_client.post(
        "/api/incidents",
        json={
            "title": "Alerta de geofence legado",
            "summary": "Pessoa em area restrita",
            "severity": "high",
            "source_kind": "geofence_alert",
            "source_key": f"geofence_alert:{alert_id}",
            "metadata": {"geofence_alert_id": alert_id},
        },
    )
    assert incident.status_code == 200, incident.text
    incident_id = incident.json()["id"]
    db.update_geofence_alert(alert_id, {"incident_id": incident_id}, "sistema")

    resolved = admin_client.patch(
        f"/geofence-alerts/{alert_id}",
        json={"status": "resolved", "resolution": "encerrado pelo mapa"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["incident_id"] == incident_id

    detail = admin_client.get(f"/api/incidents/{incident_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "closed"
    assert detail.json()["resolution"] == "encerrado pelo mapa"
    assert any(item["action"] == "incident.closed" for item in detail.json()["timeline"])


def test_closing_geofence_incident_resolves_linked_alert(admin_client):
    import db

    alert_id = db.save_geofence_alert(sample_geofence_alert())
    incident = admin_client.post(
        "/api/incidents",
        json={
            "title": "Alerta de geofence no mapa",
            "summary": "Pessoa em area restrita",
            "severity": "high",
            "source_kind": "geofence_alert",
            "source_key": f"geofence_alert:{alert_id}",
            "metadata": {"geofence_alert_id": alert_id},
        },
    )
    assert incident.status_code == 200, incident.text
    incident_id = incident.json()["id"]

    closed = admin_client.patch(
        f"/api/incidents/{incident_id}",
        json={"status": "closed", "resolution": "resolvido pela ocorrencia"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    default_list = admin_client.get("/geofence-alerts")
    assert alert_id not in {item["id"] for item in default_list.json()}

    with_resolved = admin_client.get("/geofence-alerts?include_resolved=true")
    alert = next(item for item in with_resolved.json() if item["id"] == alert_id)
    assert alert["status"] == "resolved"
    assert alert["resolution"] == "resolvido pela ocorrencia"
    assert alert["incident_id"] == incident_id
