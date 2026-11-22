from __future__ import annotations

from datetime import datetime, timezone


PNG_1X1 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def create_frame(camera_id: str = "CAM_TEST") -> int:
    import db

    frame_id = db.save_processed_frame(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "camera": {"id": camera_id, "name": camera_id},
            "frame": {"mode": "test", "width": 100, "height": 80, "image": PNG_1X1},
            "detections": [],
        }
    )
    assert frame_id
    return int(frame_id)


def test_operator_can_list_recent_annotation_frames(admin_client):
    frame_id = create_frame()

    response = admin_client.get("/annotation/frames?hours=12&limit=10")

    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert frame_id in ids


def test_operator_can_save_manual_annotation_box(admin_client):
    frame_id = create_frame()

    response = admin_client.post(
        f"/annotation/frames/{frame_id}/boxes",
        json={
            "curation_status": "approved",
            "comment": "pessoa com guarda-chuva",
            "boxes": [{"object_type": "pessoa", "bbox": {"x": 10, "y": 12, "w": 30, "h": 40}}],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["feedback_type"] == "false_negative"
    assert data["payload"]["annotation_boxes"][0]["object_type"] == "pessoa"
    assert data["curation_status"] == "approved"


def test_admin_can_export_yolo_dataset_and_create_dry_run_training_job(admin_client, tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    frame_id = create_frame()
    annotation = admin_client.post(
        f"/annotation/frames/{frame_id}/boxes",
        json={
            "curation_status": "approved",
            "boxes": [{"object_type": "pessoa", "bbox": {"x": 10, "y": 12, "w": 30, "h": 40}}],
        },
    )
    assert annotation.status_code == 200, annotation.text

    export = admin_client.post(
        "/annotation/export-yolo",
        json={"hours": 12, "dataset_version": "test_yolo_dataset", "curation_status": "approved"},
    )

    assert export.status_code == 200, export.text
    payload = export.json()
    assert payload["dataset_version"] == "test_yolo_dataset"
    assert payload["items_count"] == 1
    assert payload["data_yaml"].endswith("data.yaml")

    train = admin_client.post(
        "/annotation/train-yolo",
        json={"dataset_version": "test_yolo_dataset", "dry_run": True},
    )

    assert train.status_code == 200, train.text
    assert train.json()["status"] in {"queued", "running", "completed"}
