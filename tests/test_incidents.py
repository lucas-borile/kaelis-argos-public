from __future__ import annotations

from test_attention_queue import create_temporal_event


def test_create_manual_incident_from_frame(admin_client):
    response = admin_client.post(
        "/api/incidents",
        json={
            "title": "Pessoa caída",
            "summary": "Criada a partir do mapa",
            "severity": "critical",
            "source_kind": "frame",
            "source_key": "frame:test",
            "camera_id": "CAM_TEST",
            "frame_id": 1,
            "object_type": "pessoa",
            "metadata": {"geo": {"lat": -23.5, "lon": -46.6}},
        },
    )

    assert response.status_code == 200, response.text
    incident = response.json()
    assert incident["title"] == "Pessoa caída"
    assert incident["severity"] == "critical"
    assert incident["source_kind"] == "frame"
    assert incident["frame_id"] == 1
    assert incident["assigned_to"] == "admin"
    assert incident["metadata"]["geo"]["lat"] == -23.5
    assert incident["timeline"][0]["action"] == "incident.created"


def test_manual_map_incident_enters_attention_queue_and_can_be_resolved(admin_client):
    created = admin_client.post(
        "/api/incidents",
        json={
            "title": "Portão aberto",
            "summary": "Criada por clique no mapa",
            "severity": "high",
            "source_kind": "map",
            "source_key": "map:-23.5,-46.6",
            "metadata": {"geo": {"lat": -23.5, "lon": -46.6}},
        },
    )

    assert created.status_code == 200, created.text
    incident_id = created.json()["id"]

    queue = admin_client.get("/api/attention")
    assert queue.status_code == 200, queue.text
    item = next(row for row in queue.json()["items"] if row["item_key"] == f"incident:{incident_id}")
    assert item["kind"] == "incident"
    assert item["severity"] == "high"
    assert item["source"]["metadata"]["geo"]["lon"] == -46.6

    resolved = admin_client.patch(
        f"/api/attention/incident:{incident_id}",
        json={"status": "resolved", "resolution": "verificado no local"},
    )

    assert resolved.status_code == 200, resolved.text
    detail = admin_client.get(f"/api/incidents/{incident_id}")
    assert detail.json()["status"] == "closed"
    assert detail.json()["resolution"] == "verificado no local"


def test_geofence_incident_is_listed_and_pdf_can_be_exported(admin_client):
    created = admin_client.post(
        "/api/incidents",
        json={
            "title": "Alerta de geofence",
            "summary": "Pessoa em zona restrita",
            "severity": "high",
            "source_kind": "geofence_alert",
            "source_key": "geofence_alert:99",
            "camera_id": "CAM_TEST",
            "frame_id": 2,
            "object_type": "pessoa",
            "metadata": {
                "geo": {"lat": -23.51, "lon": -46.61},
                "alert": {"id": 99, "rule_name": "Zona restrita"},
            },
        },
    )

    assert created.status_code == 200, created.text
    incident_id = created.json()["id"]

    listed = admin_client.get("/api/incidents?status=open")
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json() if row["id"] == incident_id)
    assert item["source_kind"] == "geofence_alert"

    pdf = admin_client.get(f"/api/incidents/{incident_id}/report.pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")


def test_create_incident_from_attention_item(admin_client):
    event = create_temporal_event()
    item_key = f"temporal_event:{event['id']}"

    response = admin_client.post(f"/api/attention/{item_key}/incident", json={})

    assert response.status_code == 200, response.text
    incident = response.json()
    assert incident["id"]
    assert incident["attention_item_key"] == item_key
    assert incident["status"] == "open"
    assert incident["assigned_to"] == "admin"
    assert incident["timeline"]
    assert incident["timeline"][0]["action"] == "incident.created"

    queue = admin_client.get("/api/attention")
    item = next(row for row in queue.json()["items"] if row["item_key"] == item_key)
    assert item["status"] == "assigned"


def test_incident_detail_timeline_and_close(admin_client):
    event = create_temporal_event()
    item_key = f"temporal_event:{event['id']}"
    created = admin_client.post(f"/api/attention/{item_key}/incident", json={})
    incident_id = created.json()["id"]

    note = admin_client.post(
        f"/api/incidents/{incident_id}/timeline",
        json={"action": "note", "message": "Responsavel acionado"},
    )

    assert note.status_code == 200, note.text
    assert note.json()["message"] == "Responsavel acionado"

    detail = admin_client.get(f"/api/incidents/{incident_id}")
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["timeline"]) == 2

    closed = admin_client.patch(
        f"/api/incidents/{incident_id}",
        json={"status": "closed", "resolution": "sem risco"},
    )

    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["resolution"] == "sem risco"

    open_list = admin_client.get("/api/incidents?status=open")
    ids = {item["id"] for item in open_list.json()}
    assert incident_id not in ids
