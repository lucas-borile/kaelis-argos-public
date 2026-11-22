from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("ARGOS_ENV", "development")
os.environ.setdefault("ARGOS_CORS_ORIGINS", "http://127.0.0.1:8080")
os.environ.setdefault("RETENTION_ENABLED", "false")
os.environ.setdefault("YOLO_WARMUP", "false")
os.environ.setdefault("ENABLE_MOCK_FALLBACK", "false")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    import db
    import feature_manager
    import main

    db_path = tmp_path / "argos-test.db"
    frame_dir = tmp_path / "frames"
    static_dir = tmp_path / "static"
    floor_plan_dir = static_dir / "floor_plans"

    monkeypatch.setattr(db, "USE_POSTGRES", False)
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "FRAME_IMAGE_STORAGE_DIR", frame_dir)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "STATIC_DIR", static_dir)
    monkeypatch.setattr(main, "FLOOR_PLAN_STATIC_DIR", floor_plan_dir)
    monkeypatch.setattr(main, "FLOOR_PLAN_UPLOAD_MAX_BYTES", 512)
    monkeypatch.setattr(main, "CAMERAS", [])

    floor_plan_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    main.login_rate_attempts.clear()
    main.login_rate_lockouts.clear()
    main.latest_frames.clear()
    main.history.clear()
    feature_manager.invalidate_feature_cache()

    return TestClient(main.app)


@pytest.fixture()
def admin_client(app_client):
    response = app_client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200, response.text
    return app_client
