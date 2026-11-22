from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, authenticate, create_session, current_admin, current_operator, current_user, get_session, parse_bearer_token, revoke_session, websocket_user
from camera_gateway import (
    CameraConfig as GatewayCameraConfig,
    capture_frame as gateway_capture_frame,
    config_from_camera as gateway_config_from_camera,
    discover_onvif_cameras,
    get_camera_health as gateway_camera_health,
    get_onvif_profiles,
    public_dict as gateway_public_dict,
    scrub_url as scrub_stream_url,
    select_preferred_profile,
    test_rtsp_connection,
)
from capture_manager import CAPTURE_WORKER_MAX_STALE_S, CameraCaptureManager
from config import CAMERAS
from cors_config import is_production_environment, parse_cors_origins
from db import (
    attach_feedback_to_events,
    create_geofence,
    create_geofence_rule,
    create_floor_plan,
    create_floor_plan_camera,
    create_floor_plan_zone,
    create_app_user,
    create_feedback_annotation,
    create_feedback_dataset_export,
    create_incident,
    delete_floor_plan,
    delete_floor_plan_camera,
    delete_floor_plan_zone,
    delete_geofence,
    delete_geofence_rule,
    delete_app_user,
    create_persisted_camera,
    database_backend,
    delete_persisted_camera,
    get_frame,
    get_event,
    get_feedback_annotation,
    get_frame_image_blob,
    get_floor_plan,
    get_floor_plan_camera,
    get_floor_plan_zone,
    get_incident,
    get_incident_by_attention_key,
    get_geofence_alert,
    get_latest_frame_for_camera,
    list_floor_plan_cameras,
    list_floor_plans,
    list_floor_plan_zones,
    list_frames_for_camera,
    list_recent_frames,
    get_telegram_config,
    get_persisted_camera,
    init_db,
    acknowledge_temporal_event,
    list_feature_flag_audit_log,
    list_feature_flags,
    list_chat_messages,
    list_app_users,
    list_audit_logs,
    list_attention_item_states,
    list_object_tracks,
    list_persisted_cameras,
    list_events,
    list_events_between,
    list_feedback_annotations,
    list_geofence_alerts,
    list_geofence_rules,
    list_geofences,
    list_incident_timeline,
    list_incidents,
    list_review_events,
    list_temporal_events,
    feedback_stats,
    purge_old_frames,
    purge_orphan_frame_files,
    save_audit_log,
    save_chat_message,
    save_geofence_alert,
    add_incident_timeline,
    save_processed_frame,
    save_temporal_event,
    save_track_history,
    save_telegram_config,
    upsert_object_track,
    update_floor_plan,
    update_floor_plan_camera,
    update_floor_plan_zone,
    update_app_user,
    update_feedback_annotation,
    update_feature_flag,
    update_geofence_alert,
    update_geofence,
    update_geofence_rule,
    update_persisted_camera,
    update_incident,
    upsert_attention_item_state,
)
from frame_change_gate import FrameChangeGate
from feature_manager import disabled_response_detail, invalidate_feature_cache, is_feature_enabled, list_effective_features
from floor_plan_coverage import calculate_floor_coverage
from frame_processor import build_processed_frame, build_processed_frames_batch
from network_security import UnsafeNetworkTargetError, validate_safe_host, validate_safe_url
from priority_scheduler import PriorityInferenceScheduler
from schemas import AttentionItemUpdate, AttentionQuickConfirm, Camera, CameraConnectionTest, CameraCreate, CameraDistanceCalibration, CameraUpdate, ChatMessageCreate, Detection, FeedbackCreate, FeedbackExportRequest, FeedbackUpdate, FloorPlanCameraCreate, FloorPlanCameraUpdate, FloorPlanCreate, FloorPlanUpdate, FloorPlanZoneCreate, FloorPlanZoneUpdate, FrameAnnotationRequest, GeoFenceAlertUpdate, GeoFenceCreate, GeoFenceRuleCreate, GeoFenceRuleUpdate, GeoFenceUpdate, IncidentCreate, IncidentFromAttentionRequest, IncidentTimelineCreate, IncidentUpdate, IpCameraCreate, IpCameraUpdate, LoginRequest, LoginResponse, OnvifDiscoveryRequest, StressBenchmarkRequest, TelegramIntegrationUpdate, TelegramTestRequest, UserCreate, UserUpdate, YoloDatasetExportRequest, YoloTrainingRequest
from temporal_tracking import TemporalTracker, evaluate_geofence_rules as evaluate_temporal_geofence_rules


logger = logging.getLogger("kaelis-argos")
PROCESS_CAMERA_DELAY_S = float(os.getenv("PROCESS_CAMERA_DELAY_S", "0.0"))
PROCESS_CYCLE_PAUSE_S = float(os.getenv("PROCESS_CYCLE_PAUSE_S", "0.2"))
PROCESS_CONCURRENCY = int(os.getenv("PROCESS_CONCURRENCY", "2"))
YOLO_BATCH_ENABLED = os.getenv("YOLO_BATCH_ENABLED", "true").lower() == "true"
YOLO_BATCH_SIZE = max(1, int(os.getenv("YOLO_BATCH_SIZE", "4")))
OFFLINE_BACKOFF_BASE_S = float(os.getenv("OFFLINE_BACKOFF_BASE_S", "15"))
OFFLINE_BACKOFF_MAX_S = float(os.getenv("OFFLINE_BACKOFF_MAX_S", "120"))
CAMERA_AUTO_DEGRADE_ENABLED = os.getenv("CAMERA_AUTO_DEGRADE_ENABLED", "true").lower() == "true"
CAMERA_DEGRADE_AFTER_FAILURES = max(1, int(os.getenv("CAMERA_DEGRADE_AFTER_FAILURES", "8")))
CAMERA_DEGRADE_BACKOFF_S = float(os.getenv("CAMERA_DEGRADE_BACKOFF_S", "600"))
STORE_FRAME_IMAGES = os.getenv("STORE_FRAME_IMAGES", "true").lower() == "true"
RETENTION_ENABLED = os.getenv("RETENTION_ENABLED", "true").lower() == "true"
RETENTION_MAX_FRAME_AGE_DAYS = int(os.getenv("RETENTION_MAX_FRAME_AGE_DAYS", "7"))
RETENTION_INTERVAL_S = float(os.getenv("RETENTION_INTERVAL_S", "3600"))
DATASET_CLASS_NAMES = ["pessoa", "carro", "moto", "bicicleta", "onibus", "caminhao"]
DATASET_CLASS_IDS = {name: index for index, name in enumerate(DATASET_CLASS_NAMES)}
training_jobs: dict[str, dict] = {}
DB_PATH = Path(os.getenv("SPMU_DB_PATH", "spmu.db"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
STATIC_DIR = Path(__file__).resolve().parent / "static"
FLOOR_PLAN_STATIC_DIR = STATIC_DIR / "floor_plans"
FLOOR_PLAN_UPLOAD_MAX_BYTES = max(1, int(os.getenv("FLOOR_PLAN_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024))))
FLOOR_PLAN_MAX_PIXELS = max(1, int(os.getenv("FLOOR_PLAN_MAX_PIXELS", str(25_000_000))))
FLOOR_PLAN_MAX_SIDE_PX = max(1, int(os.getenv("FLOOR_PLAN_MAX_SIDE_PX", "20000")))
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = max(1, int(os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "5")))
LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS = max(LOGIN_RATE_LIMIT_MAX_ATTEMPTS, int(os.getenv("LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS", "25")))
LOGIN_RATE_LIMIT_WINDOW_S = max(1, int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_S", "900")))
LOGIN_RATE_LIMIT_LOCKOUT_S = max(1, int(os.getenv("LOGIN_RATE_LIMIT_LOCKOUT_S", "900")))
LOGIN_RATE_LIMIT_MAX_KEYS = max(100, int(os.getenv("LOGIN_RATE_LIMIT_MAX_KEYS", "10000")))
TRUST_PROXY_HEADERS = os.getenv("ARGOS_TRUST_PROXY_HEADERS", "false").lower() in {"1", "true", "yes", "on"}
PROCESS_STARTED_AT = datetime.now(timezone.utc)
ARGOS_BENCHMARK_LOGS = os.getenv("ARGOS_BENCHMARK_LOGS", "false").lower() == "true"
ARGOS_BENCHMARK_LOG_PATH = Path(os.getenv("ARGOS_BENCHMARK_LOG_PATH", "./logs/argos_processing.jsonl"))
ARGOS_BENCHMARK_REPORTS_PATH = Path(os.getenv("ARGOS_BENCHMARK_REPORTS_PATH", "./benchmarks/reports"))
benchmark_log_lock = threading.Lock()
ADVANCED_CAMERA_INFERENCE_FIELDS = {
    "detection_profile",
    "yolo_model",
    "yolo_imgsz",
    "person_confidence_min",
    "yolo_iou",
    "frame_access",
    "frame_access_users",
}

try:
    import psutil
except Exception:  # pragma: no cover - optional host metric dependency
    psutil = None

app = FastAPI(title="SPMU Krakow MVP", version="0.1.0")
BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
FLOOR_PLAN_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/benchmark-artifacts", StaticFiles(directory=BENCHMARKS_DIR), name="benchmark-artifacts")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)

history: deque[Detection] = deque(maxlen=500)
clients: set[WebSocket] = set()
producer_task: asyncio.Task | None = None
retention_task: asyncio.Task | None = None
latest_frames: dict[str, dict] = {}
processing_semaphore = asyncio.Semaphore(PROCESS_CONCURRENCY)
recent_geofence_alert_keys: deque[str] = deque(maxlen=1000)
camera_next_due: dict[str, float] = {}
camera_failures: dict[str, int] = {}
degraded_cameras: dict[str, dict] = {}
active_geofence_cache: dict[str, object] = {"expires_at": 0.0, "geofences": [], "rules": []}
capture_manager = CameraCaptureManager()
frame_change_gate = FrameChangeGate()
priority_scheduler = PriorityInferenceScheduler()
temporal_tracker = TemporalTracker()
benchmark_jobs: dict[str, dict] = {}
login_rate_attempts: dict[str, deque[float]] = {}
login_rate_lockouts: dict[str, float] = {}
login_rate_lock = threading.Lock()


@app.on_event("startup")
async def startup() -> None:
    global producer_task, retention_task
    init_db()
    await refresh_camera_cache()
    if RETENTION_ENABLED:
        await asyncio.to_thread(run_retention_once)
    if producer_task is None:
        producer_task = asyncio.create_task(processing_loop())
    if RETENTION_ENABLED and retention_task is None:
        retention_task = asyncio.create_task(retention_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if producer_task:
        producer_task.cancel()
    if retention_task:
        retention_task.cancel()
    await capture_manager.stop_all()


async def refresh_camera_cache() -> list[dict]:
    cameras = await asyncio.to_thread(list_persisted_cameras)
    CAMERAS[:] = cameras
    return cameras


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def require_feature(feature_key: str) -> None:
    if not is_feature_enabled(feature_key):
        raise HTTPException(status_code=403, detail=disabled_response_detail(feature_key))


@app.get("/system/status")
async def system_status(user: dict = Depends(current_user)) -> dict:
    return await asyncio.to_thread(build_system_status)


@app.get("/api/cameras/health")
async def camera_health_summary(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
) -> dict:
    return await asyncio.to_thread(build_camera_health_summary, hours, limit)


@app.get("/api/admin/features")
async def admin_features(user: dict = Depends(current_admin)) -> dict:
    features = await asyncio.to_thread(list_effective_features)
    return {"features": features}


@app.patch("/api/admin/features/{feature_key}")
async def admin_feature_update(feature_key: str, payload: dict, request: Request, user: dict = Depends(current_admin)) -> dict:
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled is required")
    reason = str(payload.get("reason") or "").strip()
    try:
        item = await asyncio.to_thread(update_feature_flag, feature_key, bool(payload["enabled"]), user.get("username", ""), reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_feature_cache()
    await audit(
        user,
        "feature.update",
        feature_key,
        {"enabled": item["enabled"], "reason": reason},
        request,
    )
    return item


@app.get("/api/admin/features/audit-log")
async def admin_feature_audit_log(feature_key: str | None = None, limit: int = 100, user: dict = Depends(current_admin)) -> list[dict]:
    return await asyncio.to_thread(list_feature_flag_audit_log, min(max(limit, 1), 500), feature_key)


@app.post("/benchmarks/stress")
async def start_stress_benchmark(
    payload: StressBenchmarkRequest,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("benchmarks_enabled")
    running = [item for item in benchmark_jobs.values() if item.get("status") in {"queued", "running"}]
    if running:
        raise HTTPException(status_code=409, detail="benchmark already running")
    source_path = resolve_benchmark_source(payload.source)
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f"source not found: {payload.source}")
    job_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "ended_at": None,
        "created_by": user.get("username", ""),
        "request": payload.model_dump(),
        "source_path": str(source_path),
        "scenarios": [],
        "comparison": None,
        "log": "",
        "error": None,
    }
    benchmark_jobs[job_id] = job
    asyncio.create_task(run_stress_benchmark_job(job_id, payload, source_path))
    await audit(
        user,
        "benchmark.stress.start",
        "benchmark",
        {"job_id": job_id, **payload.model_dump()},
        request,
    )
    return public_benchmark_job(job)


@app.get("/benchmarks/stress/jobs")
async def list_stress_benchmark_jobs(user: dict = Depends(current_user)) -> list[dict]:
    require_feature("benchmarks_enabled")
    jobs = sorted(benchmark_jobs.values(), key=lambda item: item.get("created_at") or "", reverse=True)
    return [public_benchmark_job(item) for item in jobs[:20]]


@app.get("/benchmarks/stress/jobs/{job_id}")
async def get_stress_benchmark_job(job_id: str, user: dict = Depends(current_user)) -> dict:
    require_feature("benchmarks_enabled")
    job = benchmark_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="benchmark job not found")
    return public_benchmark_job(job)


@app.get("/integrations/telegram")
async def telegram_integration(user: dict = Depends(current_user)) -> dict:
    require_feature("telegram_enabled")
    return await asyncio.to_thread(get_telegram_config, True)


@app.put("/integrations/telegram")
async def telegram_integration_update(
    payload: TelegramIntegrationUpdate,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("telegram_enabled")
    try:
        item = await asyncio.to_thread(save_telegram_config, payload.model_dump(), user.get("username", ""))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit(
        user,
        "integration.telegram.update",
        "telegram",
        {
            "enabled": item["enabled"],
            "configured": item["configured"],
            "chat_id": item["chat_id"],
            "parse_mode": item["parse_mode"],
        },
        request,
    )
    return item


@app.post("/integrations/telegram/test")
async def telegram_integration_test(
    payload: TelegramTestRequest,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("telegram_enabled")
    result = await asyncio.to_thread(send_telegram_message, html.escape(payload.message))
    await audit(user, "integration.telegram.test", "telegram", result, request)
    if not result.get("sent"):
        raise HTTPException(status_code=400, detail=result.get("reason", "telegram send failed"))
    return result


@app.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    try:
        check_login_rate_limit(request, payload.username)
    except HTTPException as exc:
        await audit(
            {"username": payload.username, "role": "anonymous"},
            "auth.login_rate_limited",
            "auth",
            {"username": payload.username, "retry_after": exc.headers.get("Retry-After") if exc.headers else None},
            request,
        )
        raise
    user = authenticate(payload.username, payload.password)
    if not user:
        retry_after = record_failed_login_attempt(request, payload.username)
        await audit(
            {"username": payload.username, "role": "anonymous"},
            "auth.login_failed",
            "auth",
            {"username": payload.username, "lockout_after_failure_s": retry_after or None},
            request,
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    clear_login_rate_limit(request, payload.username)
    session = create_session(user)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session["token"],
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=is_production_environment(),
        samesite="lax",
        path="/",
    )
    await audit(user, "auth.login", "auth", {"username": user["username"]}, request)
    return {**session, **user}


@app.get("/auth/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {key: user[key] for key in ("username", "role", "name", "created_at", "expires_at") if key in user}


@app.post("/auth/logout")
async def logout(request: Request, response: Response, user: dict = Depends(current_user)) -> dict:
    tokens = {
        item
        for item in (
            request.cookies.get(SESSION_COOKIE_NAME),
            parse_bearer_token(request.headers.get("authorization")),
        )
        if item
    }
    revoked = False
    for token in tokens:
        revoked = revoke_session(token) or revoked
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax", secure=is_production_environment())
    await audit(user, "auth.logout", "auth", {"revoked": revoked}, request)
    return {"revoked": revoked}


@app.get("/users")
async def users(user: dict = Depends(current_admin)) -> list[dict]:
    return await asyncio.to_thread(list_app_users)


@app.post("/users")
async def user_create(payload: UserCreate, request: Request, user: dict = Depends(current_admin)) -> dict:
    try:
        item = await asyncio.to_thread(create_app_user, payload.model_dump(), user.get("username", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="user could not be created") from exc
    await audit(user, "user.create", item["username"], {"role": item["role"], "active": item["active"]}, request)
    return item


@app.patch("/users/{username}")
async def user_update(username: str, payload: UserUpdate, request: Request, user: dict = Depends(current_admin)) -> dict:
    changes = {key: value for key, value in payload.model_dump().items() if value is not None}
    if username == user.get("username") and changes.get("active") is False:
        raise HTTPException(status_code=400, detail="admin cannot deactivate own account")
    if username == user.get("username") and changes.get("role") and changes.get("role") != "admin":
        raise HTTPException(status_code=400, detail="admin cannot downgrade own role")
    item = await asyncio.to_thread(update_app_user, username, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="user not found")
    audit_changes = {key: value for key, value in changes.items() if key != "password"}
    if "password" in changes:
        audit_changes["password_changed"] = True
    await audit(user, "user.update", username, audit_changes, request)
    return item


@app.delete("/users/{username}")
async def user_delete(username: str, request: Request, user: dict = Depends(current_admin)) -> dict:
    if username == user.get("username"):
        raise HTTPException(status_code=400, detail="admin cannot delete own account")
    deleted = await asyncio.to_thread(delete_app_user, username)
    if not deleted:
        raise HTTPException(status_code=404, detail="user not found")
    await audit(user, "user.delete", username, {}, request)
    return {"deleted": True}


@app.get("/audit-logs")
async def audit_logs(
    limit: int = 200,
    actor: str | None = None,
    action: str | None = None,
    user: dict = Depends(current_admin),
) -> list[dict]:
    return await asyncio.to_thread(list_audit_logs, min(max(limit, 1), 1000), actor, action)


@app.get("/chat/messages")
async def chat_messages(limit: int = 100, user: dict = Depends(current_operator)) -> list[dict]:
    require_feature("chat_enabled")
    return await asyncio.to_thread(list_chat_messages, min(max(limit, 1), 300))


@app.get("/chat/mentions")
async def chat_mentions(user: dict = Depends(current_operator)) -> dict:
    require_feature("chat_enabled")
    users, geofences = await asyncio.gather(
        asyncio.to_thread(list_app_users),
        asyncio.to_thread(list_geofences),
    )
    return {
        "users": [
            {
                "username": item["username"],
                "name": item["name"],
                "role": item["role"],
            }
            for item in users
            if item.get("active") and item.get("role") in {"admin", "operator"}
        ],
        "geofences": [
            {
                "id": item["id"],
                "name": item["name"],
                "priority_level": item.get("priority_level", 3),
                "active": item.get("active", True),
            }
            for item in geofences
        ],
        "cameras": [
            {
                "id": item["id"],
                "name": item["name"],
                "active": item.get("active", True),
            }
            for item in CAMERAS
        ],
    }


@app.post("/chat/messages")
async def chat_message_create(payload: ChatMessageCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("chat_enabled")
    mentions = extract_chat_mentions(payload.body)
    message = await asyncio.to_thread(
        save_chat_message,
        user.get("username", ""),
        user.get("role", ""),
        payload.body.strip(),
        mentions,
    )
    await audit(user, "chat.message", str(message["id"]), {"mentions": mentions}, request)
    await broadcast({"event": "chat_message", "message": message})
    return message


@app.get("/cameras", response_model=list[Camera])
async def list_cameras(user: dict = Depends(current_user)) -> list[dict]:
    await refresh_camera_cache()
    return [public_camera(item) for item in CAMERAS]


@app.post("/cameras", response_model=Camera)
async def create_camera(camera: CameraCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    payload = camera.model_dump()
    strip_advanced_camera_fields_for_non_admin(payload, user)
    camera_id = payload["id"] or slug_camera_id(payload["name"])
    if await asyncio.to_thread(get_persisted_camera, camera_id):
        raise HTTPException(status_code=409, detail="camera id already exists")

    url = payload["url"]
    source_url = payload["source_url"] or url
    original_stream_url = payload["original_stream_url"] or url
    item = {
        **payload,
        "id": camera_id,
        "url": url,
        "source_url": source_url,
        "original_stream_url": original_stream_url,
    }
    item = await asyncio.to_thread(create_persisted_camera, item, user.get("username", ""))
    await refresh_camera_cache()
    await audit(user, "camera.create", item["id"], scrub_camera_for_audit(item), request)
    return public_camera(item)


@app.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str, request: Request, user: dict = Depends(current_operator)) -> dict:
    removed = await asyncio.to_thread(delete_persisted_camera, camera_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="camera not found")
    await refresh_camera_cache()
    latest_frames.pop(camera_id, None)
    camera_next_due.pop(camera_id, None)
    camera_failures.pop(camera_id, None)
    await capture_manager.stop_camera(camera_id)
    await broadcast({"event": "camera_deleted", "camera_id": camera_id})
    await audit(user, "camera.delete", camera_id, scrub_camera_for_audit(removed), request)
    return {"deleted": True, "camera": public_camera(removed)}


@app.patch("/cameras/{camera_id}", response_model=Camera)
async def update_camera(camera_id: str, camera: CameraUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    item = await asyncio.to_thread(get_persisted_camera, camera_id)
    if item is None:
        raise HTTPException(status_code=404, detail="camera not found")

    changes = camera.model_dump(exclude_unset=True)
    strip_advanced_camera_fields_for_non_admin(changes, user)
    if "url" in changes:
        changes.setdefault("source_url", changes["url"])
        changes.setdefault("original_stream_url", changes["url"])
    before = {key: item.get(key) for key in changes.keys()}
    item = await asyncio.to_thread(update_persisted_camera, camera_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="camera not found")
    await refresh_camera_cache()
    if item.get("active", True):
        camera_next_due[item["id"]] = 0.0
        camera_failures[item["id"]] = 0
    else:
        await capture_manager.stop_camera(camera_id)
    latest_frames.pop(camera_id, None)
    await broadcast({"event": "camera_updated", "camera": public_camera(item)})
    await audit(user, "camera.update", camera_id, {"before": before, "after": scrub_camera_for_audit(changes)}, request)
    return public_camera(item)


@app.get("/api/cameras")
async def api_list_ip_cameras(user: dict = Depends(current_user)) -> list[dict]:
    await refresh_camera_cache()
    return [public_ip_camera(item) for item in CAMERAS]


@app.post("/api/cameras")
async def api_create_ip_camera(payload: IpCameraCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    data = payload.model_dump()
    camera_id = data["id"] or slug_camera_id(data["name"])
    if await asyncio.to_thread(get_persisted_camera, camera_id):
        raise HTTPException(status_code=409, detail="camera id already exists")

    stream_url, gateway = await asyncio.to_thread(resolve_ip_camera_stream, data, data.get("password") or "")
    health = await asyncio.to_thread(test_rtsp_connection, stream_url)
    gateway["status"] = health.status
    gateway["last_health"] = gateway_public_dict(health)
    item = ip_camera_payload(camera_id, data, stream_url, gateway, protect_camera_password(data.get("password") or ""))
    item = await asyncio.to_thread(create_persisted_camera, item, user.get("username", ""))
    await refresh_camera_cache()
    await audit(user, "camera.ip.create", item["id"], scrub_camera_for_audit(public_ip_camera(item)), request)
    return public_ip_camera(item)


@app.post("/api/cameras/discover-onvif")
async def api_discover_onvif(payload: OnvifDiscoveryRequest | None = None, user: dict = Depends(current_operator)) -> list[dict]:
    require_feature("camera_discovery_enabled")
    options = payload or OnvifDiscoveryRequest()
    items = await asyncio.to_thread(discover_onvif_cameras, options.timeout_s)
    return [gateway_public_dict(item) for item in items]


@app.post("/api/cameras/test-rtsp")
async def api_test_rtsp_url(payload: CameraConnectionTest, user: dict = Depends(current_operator)) -> dict:
    if not payload.rtsp_url:
        raise HTTPException(status_code=400, detail="rtsp_url is required")
    health = await asyncio.to_thread(test_rtsp_connection, payload.rtsp_url)
    return gateway_public_dict(health)


@app.get("/api/cameras/{camera_id}")
async def api_get_ip_camera(camera_id: str, user: dict = Depends(current_user)) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return public_ip_camera(camera)


@app.put("/api/cameras/{camera_id}")
async def api_update_ip_camera(camera_id: str, payload: IpCameraUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    data = payload.model_dump(exclude_unset=True)
    gateway = dict(camera.get("camera_gateway") or {})
    for key in (
        "host",
        "rtsp_port",
        "onvif_port",
        "username",
        "rtsp_url_manual",
        "manufacturer",
        "model",
        "selected_profile_token",
        "selected_stream_role",
    ):
        if key in data:
            gateway[key] = data[key] or ""

    changes: dict = {}
    for key in ("name", "lat", "lon", "azimuth_deg", "active"):
        if key in data:
            changes[key] = data[key]

    stream_url = str(camera.get("original_stream_url") or camera.get("url") or "")
    needs_stream_refresh = any(key in data for key in ("host", "rtsp_port", "onvif_port", "username", "password", "rtsp_url_manual", "selected_profile_token", "selected_stream_role"))
    if needs_stream_refresh:
        merged = {
            "name": data.get("name") or camera.get("name"),
            "host": gateway.get("host", ""),
            "rtsp_port": gateway.get("rtsp_port") or 554,
            "onvif_port": gateway.get("onvif_port") or 80,
            "username": gateway.get("username", ""),
            "password": data.get("password") or password_for_camera(camera),
            "rtsp_url_manual": gateway.get("rtsp_url_manual", ""),
            "manufacturer": gateway.get("manufacturer", ""),
            "model": gateway.get("model", ""),
            "use_onvif": True,
            "selected_profile_token": gateway.get("selected_profile_token", ""),
            "selected_stream_role": gateway.get("selected_stream_role", "substream"),
        }
        stream_url, gateway = await asyncio.to_thread(resolve_ip_camera_stream, merged, merged.get("password") or "")
        changes.update({"url": stream_url, "source_url": stream_url, "original_stream_url": stream_url})
        if "password" in data:
            changes["camera_secret"] = protect_camera_password(data.get("password") or "")

    changes["camera_gateway"] = gateway
    updated = await asyncio.to_thread(update_persisted_camera, camera_id, changes)
    if updated is None:
        raise HTTPException(status_code=404, detail="camera not found")
    await refresh_camera_cache()
    latest_frames.pop(camera_id, None)
    camera_next_due[camera_id] = 0.0
    await audit(user, "camera.ip.update", camera_id, scrub_camera_for_audit(public_ip_camera(updated)), request)
    return public_ip_camera(updated)


@app.delete("/api/cameras/{camera_id}")
async def api_delete_ip_camera(camera_id: str, request: Request, user: dict = Depends(current_operator)) -> dict:
    return await delete_camera(camera_id, request, user)


@app.post("/api/cameras/{camera_id}/test")
async def api_test_ip_camera(camera_id: str, payload: CameraConnectionTest | None = None, request: Request = None, user: dict = Depends(current_operator)) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    body = payload or CameraConnectionTest()
    rtsp_url = body.rtsp_url or camera.get("original_stream_url") or camera.get("url") or ""
    health = await asyncio.to_thread(test_rtsp_connection, rtsp_url)
    gateway = dict(camera.get("camera_gateway") or {})
    gateway["status"] = health.status
    gateway["last_health"] = gateway_public_dict(health)
    await asyncio.to_thread(update_persisted_camera, camera_id, {"camera_gateway": gateway})
    await refresh_camera_cache()
    await audit(user, "camera.ip.test", camera_id, gateway_public_dict(health), request)
    return gateway_public_dict(health)


@app.get("/api/cameras/{camera_id}/profiles")
async def api_get_ip_camera_profiles(camera_id: str, user: dict = Depends(current_operator)) -> list[dict]:
    require_feature("camera_discovery_enabled")
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    password = password_for_camera(camera)
    config = gateway_config_from_camera(camera, password=password)
    if not config.host:
        raise HTTPException(status_code=400, detail="camera host is required for ONVIF profiles")
    if config.username and not password:
        raise HTTPException(status_code=400, detail="camera password is not available; update the camera with credentials")
    try:
        profiles = await asyncio.to_thread(get_onvif_profiles, config)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ONVIF unavailable: {exc}") from exc
    return [gateway_public_dict(item) for item in profiles]


@app.post("/api/cameras/{camera_id}/snapshot")
async def api_ip_camera_snapshot(camera_id: str, user: dict = Depends(current_operator)) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    result = await asyncio.to_thread(gateway_capture_frame, camera_id, camera.get("original_stream_url") or camera.get("url") or "")
    return gateway_public_dict(result)


@app.get("/api/cameras/{camera_id}/health")
async def api_ip_camera_health(camera_id: str, user: dict = Depends(current_user)) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    health = await asyncio.to_thread(gateway_camera_health, camera_id, camera.get("original_stream_url") or camera.get("url") or "")
    return gateway_public_dict(health)


@app.post("/cameras/{camera_id}/calibrate-distance", response_model=Camera)
async def calibrate_camera_distance(
    camera_id: str,
    payload: CameraDistanceCalibration,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    camera = await asyncio.to_thread(get_persisted_camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", camera_id, {"camera_id": camera_id, "scope": "distance_calibration"}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    frame_width = max(1.0, float(camera.get("frame_width") or 1280))
    frame = None
    if payload.frame_id:
        frame = await asyncio.to_thread(get_frame, int(payload.frame_id))
        if frame and str(frame.get("camera_id") or frame.get("camera", {}).get("id") or "") != camera_id:
            raise HTTPException(status_code=400, detail="frame does not belong to camera")
    frame_meta = (frame or {}).get("frame") or {}
    frame_width_at_detection = max(1.0, float(frame_meta.get("width") or frame_width))
    focal_for_frame = (payload.known_distance_m * payload.bbox_height_px) / payload.known_height_m
    focal_for_camera_width = focal_for_frame * (frame_width / frame_width_at_detection)
    focal_for_camera_width = round(max(50.0, min(focal_for_camera_width, 10000.0)), 2)
    updated = await asyncio.to_thread(update_persisted_camera, camera_id, {"focal_length_px": focal_for_camera_width})
    if updated is None:
        raise HTTPException(status_code=404, detail="camera not found")
    await refresh_camera_cache()
    camera_next_due[camera_id] = 0.0
    latest_frames.pop(camera_id, None)
    details = {
        "before_focal_length_px": camera.get("focal_length_px"),
        "after_focal_length_px": focal_for_camera_width,
        "known_height_m": payload.known_height_m,
        "known_distance_m": payload.known_distance_m,
        "bbox_height_px": payload.bbox_height_px,
        "object_type": payload.object_type,
        "frame_id": payload.frame_id,
    }
    await audit(user, "camera.distance_calibrate", camera_id, details, request)
    await broadcast({"event": "camera_updated", "camera": public_camera(updated)})
    return updated


def strip_advanced_camera_fields_for_non_admin(payload: dict, user: dict) -> None:
    if user.get("role") == "admin":
        return
    for field in ADVANCED_CAMERA_INFERENCE_FIELDS:
        payload.pop(field, None)


def get_camera_from_cache(camera_id: str) -> dict | None:
    return next((item for item in CAMERAS if item["id"] == camera_id), None)


def can_user_view_camera_frame(user: dict, camera: dict | None) -> bool:
    if camera is None:
        return True
    if user.get("role") == "admin":
        return True
    access = str(camera.get("frame_access") or "all")
    if access == "all":
        return True
    if access == "operator":
        return user.get("role") in {"operator", "admin"}
    if access == "admin":
        return user.get("role") == "admin"
    if access == "users":
        allowed = {str(item).strip() for item in camera.get("frame_access_users") or [] if str(item).strip()}
        return str(user.get("username") or "") in allowed
    return True


@app.get("/detections", response_model=list[Detection])
async def list_detections(cam: str | None = None, type: str | None = None, user: dict = Depends(current_user)) -> list[Detection]:
    results = list(history)
    if cam:
        results = [item for item in results if item.camera_id == cam]
    if type:
        results = [item for item in results if item.object_type == type]
    return results[-200:]


@app.get("/detections/stats")
async def detection_stats(user: dict = Depends(current_user)) -> dict:
    counts: dict[str, int] = {}
    for item in history:
        counts[item.object_type] = counts.get(item.object_type, 0) + 1
    return {"total": len(history), "by_type": counts}


@app.get("/detections/heatmap")
async def detection_heatmap(user: dict = Depends(current_user)) -> list[dict]:
    require_feature("heatmap_enabled")
    return [
        {
            "lat": item.geo.lat,
            "lon": item.geo.lon,
            "weight": round(item.confidence, 2),
            "type": item.object_type,
        }
        for item in list(history)[-200:]
    ]


@app.get("/events")
async def events(limit: int = 100, user: dict = Depends(current_user)) -> list[dict]:
    return await asyncio.to_thread(list_events, min(max(limit, 1), 500))


@app.get("/events/range")
async def events_range(
    start: str,
    end: str,
    limit: int = 2000,
    object_type: str | None = None,
    confidence_min: float | None = None,
    track_id: str | None = None,
    upper_color: str | None = None,
    lower_color: str | None = None,
    vehicle_color: str | None = None,
    attribute_confidence_min: float = 0.55,
    camera_id: list[str] = Query(default_factory=list),
    request: Request = None,
    user: dict = Depends(current_user),
) -> list[dict]:
    if not start or not end:
        raise HTTPException(status_code=400, detail="start and end are required")
    start_iso = normalize_event_timestamp(start)
    end_iso = normalize_event_timestamp(end)
    if start_iso > end_iso:
        raise HTTPException(status_code=400, detail="start must be before end")
    if confidence_min is not None and not 0 <= confidence_min <= 1:
        raise HTTPException(status_code=400, detail="confidence_min must be between 0 and 1")
    if not 0 <= attribute_confidence_min <= 1:
        raise HTTPException(status_code=400, detail="attribute_confidence_min must be between 0 and 1")
    result = await asyncio.to_thread(
        list_events_between,
        start_iso,
        end_iso,
        min(max(limit, 1), 5000),
        object_type,
        confidence_min,
        camera_id,
        track_id,
        upper_color,
        lower_color,
        vehicle_color,
        attribute_confidence_min,
    )
    await audit(
        user,
        "review.search",
        "events/range",
        {
            "start": start_iso,
            "end": end_iso,
            "limit": min(max(limit, 1), 5000),
            "object_type": object_type,
            "confidence_min": confidence_min,
            "track_id": track_id,
            "upper_color": upper_color,
            "lower_color": lower_color,
            "vehicle_color": vehicle_color,
            "attribute_confidence_min": attribute_confidence_min,
            "camera_id": camera_id,
            "result_count": len(result),
        },
        request,
    )
    return result


@app.get("/review/events")
async def review_events(
    start: str,
    end: str,
    limit: int = Query(500, ge=1, le=5000),
    object_type: str | None = None,
    confidence_min: float | None = Query(None, ge=0, le=1),
    confidence_max: float | None = Query(None, ge=0, le=1),
    track_id: str | None = None,
    feedback_state: str | None = Query(None, pattern="^(with|without)?$"),
    feedback_type: str | None = None,
    curation_status: str | None = None,
    upper_color: str | None = None,
    lower_color: str | None = None,
    vehicle_color: str | None = None,
    attribute_confidence_min: float = Query(0.55, ge=0, le=1),
    camera_id: list[str] = Query(default_factory=list),
    request: Request = None,
    user: dict = Depends(current_user),
) -> list[dict]:
    require_feature("human_feedback_enabled")
    start_iso = normalize_event_timestamp(start)
    end_iso = normalize_event_timestamp(end)
    if start_iso > end_iso:
        raise HTTPException(status_code=400, detail="start must be before end")
    if confidence_min is not None and confidence_max is not None and confidence_min > confidence_max:
        raise HTTPException(status_code=400, detail="confidence_min must be <= confidence_max")
    result = await asyncio.to_thread(
        list_review_events,
        start_iso,
        end_iso,
        limit,
        object_type,
        confidence_min,
        confidence_max,
        camera_id,
        track_id,
        feedback_state,
        feedback_type,
        curation_status,
        upper_color,
        lower_color,
        vehicle_color,
        attribute_confidence_min,
    )
    await audit(
        user,
        "review.search",
        "review/events",
        {
            "start": start_iso,
            "end": end_iso,
            "limit": limit,
            "object_type": object_type,
            "confidence_min": confidence_min,
            "confidence_max": confidence_max,
            "track_id": track_id,
            "feedback_state": feedback_state,
            "feedback_type": feedback_type,
            "curation_status": curation_status,
            "camera_id": camera_id,
            "result_count": len(result),
        },
        request,
    )
    return result


@app.get("/review/next")
async def review_next(
    start: str | None = None,
    end: str | None = None,
    object_type: str | None = None,
    camera_id: list[str] = Query(default_factory=list),
    user: dict = Depends(current_user),
) -> dict:
    require_feature("human_feedback_enabled")
    end_dt = datetime.now(timezone.utc) if not end else datetime.fromisoformat(normalize_event_timestamp(end))
    start_dt = end_dt - timedelta(hours=24) if not start else datetime.fromisoformat(normalize_event_timestamp(start))
    events = await asyncio.to_thread(
        list_review_events,
        start_dt.isoformat(),
        end_dt.isoformat(),
        1,
        object_type,
        None,
        None,
        camera_id,
        None,
        "without",
        None,
        None,
        None,
        None,
        None,
        0.55,
    )
    return events[0] if events else {}


@app.get("/review/events/{event_id}")
async def review_event_by_id(event_id: int, user: dict = Depends(current_user)) -> dict:
    require_feature("human_feedback_enabled")
    event = await asyncio.to_thread(get_event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return (await asyncio.to_thread(attach_feedback_to_events, [event]))[0]


@app.post("/review/events/{event_id}/mark")
async def review_event_mark(event_id: int, payload: dict, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("human_feedback_enabled")
    event = await asyncio.to_thread(get_event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    feedback_type = payload.get("feedback_type") or ("event_relevant" if payload.get("relevant", True) else "event_irrelevant")
    feedback_payload = {
        "feedback_type": feedback_type,
        "comment": payload.get("comment") or "",
        "curation_status": payload.get("curation_status") or "pending",
        "payload": {"source": "review_mark", **(payload.get("payload") or {})},
    }
    item = await asyncio.to_thread(create_feedback_annotation, event_id, feedback_payload, user)
    await audit(user, "feedback.create", f"event:{event_id}", {"feedback_id": item.get("id"), "feedback_type": feedback_type}, request)
    return item


@app.post("/events/{event_id}/feedback")
async def event_feedback_create(event_id: int, payload: FeedbackCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("human_feedback_enabled")
    event = await asyncio.to_thread(get_event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    item = await asyncio.to_thread(create_feedback_annotation, event_id, payload.model_dump(), user)
    await audit(
        user,
        "feedback.create",
        f"event:{event_id}",
        {"feedback_id": item.get("id"), "feedback_type": item.get("feedback_type"), "curation_status": item.get("curation_status")},
        request,
    )
    return item


@app.get("/events/{event_id}/feedback")
async def event_feedback_list(event_id: int, user: dict = Depends(current_user)) -> list[dict]:
    require_feature("human_feedback_enabled")
    event = await asyncio.to_thread(get_event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return await asyncio.to_thread(list_feedback_annotations, event_id, None, None, None, None, None, None, False, 500)


@app.get("/feedback")
async def feedback_list(
    start: str | None = None,
    end: str | None = None,
    camera_id: str | None = None,
    feedback_type: str | None = None,
    reviewer_id: str | None = None,
    curation_status: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(500, ge=1, le=5000),
    user: dict = Depends(current_operator),
) -> list[dict]:
    require_feature("human_feedback_enabled")
    start_iso = normalize_event_timestamp(start) if start else None
    end_iso = normalize_event_timestamp(end) if end else None
    return await asyncio.to_thread(
        list_feedback_annotations,
        None,
        start_iso,
        end_iso,
        camera_id,
        feedback_type,
        reviewer_id,
        curation_status,
        include_deleted,
        limit,
    )


@app.get("/feedback/stats")
async def feedback_statistics(
    start: str | None = None,
    end: str | None = None,
    camera_id: str | None = None,
    object_type: str | None = None,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("human_feedback_enabled")
    start_iso = normalize_event_timestamp(start) if start else None
    end_iso = normalize_event_timestamp(end) if end else None
    return await asyncio.to_thread(feedback_stats, start_iso, end_iso, camera_id, object_type)


def safe_dataset_version(value: str | None, prefix: str = "argos_yolo") -> str:
    version = (value or "").strip() or f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", version)[:80]


def clamp_bbox(box: dict, width: int, height: int) -> dict[str, int] | None:
    x = max(0, min(int(round(float(box.get("x", 0)))), max(width - 1, 0)))
    y = max(0, min(int(round(float(box.get("y", 0)))), max(height - 1, 0)))
    w = max(0, min(int(round(float(box.get("w", 0)))), width - x))
    h = max(0, min(int(round(float(box.get("h", 0)))), height - y))
    if w < 2 or h < 2:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def yolo_line(object_type: str, bbox: dict, width: int, height: int) -> str | None:
    class_id = DATASET_CLASS_IDS.get(object_type)
    clean = clamp_bbox(bbox, width, height)
    if class_id is None or clean is None or width <= 0 or height <= 0:
        return None
    cx = (clean["x"] + clean["w"] / 2) / width
    cy = (clean["y"] + clean["h"] / 2) / height
    bw = clean["w"] / width
    bh = clean["h"] / height
    return f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def feedback_annotation_boxes(item: dict) -> list[dict]:
    payload = item.get("payload") or {}
    if isinstance(payload.get("annotation_boxes"), list):
        return payload["annotation_boxes"]
    corrected_box = payload.get("corrected_bbox")
    object_type = item.get("corrected_value") if item.get("feedback_type") == "false_negative" else ""
    object_type = object_type if object_type in DATASET_CLASS_IDS else payload.get("object_type")
    if corrected_box and object_type in DATASET_CLASS_IDS:
        return [{"object_type": object_type, "bbox": corrected_box, "source": payload.get("source", "human_review")}]
    return []


def write_yolo_dataset(payload: YoloDatasetExportRequest, user: dict) -> dict:
    end_iso = normalize_event_timestamp(payload.end) if payload.end else datetime.now(timezone.utc).isoformat()
    if payload.start:
        start_iso = normalize_event_timestamp(payload.start)
    else:
        start_iso = (datetime.fromisoformat(end_iso.replace("Z", "+00:00")) - timedelta(hours=payload.hours or 12)).isoformat()
    rows = list_feedback_annotations(
        None,
        start_iso,
        end_iso,
        None,
        None,
        None,
        payload.curation_status,
        False,
        50_000,
    )
    camera_filter = set(payload.camera_id)
    rows = [
        row
        for row in rows
        if (not camera_filter or row.get("camera_id") in camera_filter)
        and (payload.include_sensitive or row.get("feedback_type") != "privacy_sensitive")
        and (payload.include_sensitive or row.get("curation_status") != "privacy_blocked")
        and feedback_annotation_boxes(row)
    ]
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        frame_id = row.get("frame_id")
        if frame_id:
            grouped.setdefault(int(frame_id), []).extend(feedback_annotation_boxes(row))

    safe_version = safe_dataset_version(payload.dataset_version)
    output_dir = PROJECT_ROOT / "datasets" / safe_version
    image_dirs = {"train": output_dir / "images" / "train", "val": output_dir / "images" / "val"}
    label_dirs = {"train": output_dir / "labels" / "train", "val": output_dir / "labels" / "val"}
    for path in [*image_dirs.values(), *label_dirs.values()]:
        path.mkdir(parents=True, exist_ok=True)

    items = []
    frame_ids = sorted(grouped)
    split_cutoff = max(1, int(len(frame_ids) * payload.train_split)) if len(frame_ids) > 1 else len(frame_ids)
    for index, frame_id in enumerate(frame_ids):
        frame = get_frame(frame_id)
        image = get_frame_image_blob(frame_id)
        if not frame or not image:
            continue
        width = int((frame.get("frame") or {}).get("width") or frame.get("width") or 0)
        height = int((frame.get("frame") or {}).get("height") or frame.get("height") or 0)
        lines = [
            line
            for box in grouped[frame_id]
            if (line := yolo_line(str(box.get("object_type") or ""), box.get("bbox") or {}, width, height))
        ]
        if not lines:
            continue
        split = "train" if index < split_cutoff else "val"
        stem = f"frame_{frame_id}"
        extension = ".jpg" if (image.get("content_type") or "").lower() in {"image/jpeg", "image/jpg"} else ".png"
        image_path = image_dirs[split] / f"{stem}{extension}"
        label_path = label_dirs[split] / f"{stem}.txt"
        if image.get("path"):
            shutil.copyfile(image["path"], image_path)
        else:
            image_path.write_bytes(image.get("bytes") or b"")
        label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        items.append({"frame_id": frame_id, "camera_id": frame.get("camera_id"), "split": split, "labels": len(lines), "image": str(image_path.relative_to(PROJECT_ROOT)), "label": str(label_path.relative_to(PROJECT_ROOT))})

    data_yaml = output_dir / "data.yaml"
    data_yaml.write_text(
        "\n".join(
            [
                f"path: {output_dir}",
                "train: images/train",
                "val: images/val",
                f"names: {json.dumps(DATASET_CLASS_NAMES, ensure_ascii=False)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    metadata = {
        "dataset_version": safe_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user.get("username", ""),
        "format": "yolo",
        "classes": DATASET_CLASS_NAMES,
        "filters": payload.model_dump(),
        "items_count": len(items),
        "items": items,
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    create_feedback_dataset_export(safe_version, user.get("username", ""), payload.model_dump(), str(metadata_path.relative_to(PROJECT_ROOT)), items)
    return {"dataset_version": safe_version, "items_count": len(items), "output_dir": str(output_dir.relative_to(PROJECT_ROOT)), "data_yaml": str(data_yaml.relative_to(PROJECT_ROOT))}


def run_training_job(job_id: str, request_payload: dict) -> None:
    job = training_jobs[job_id]
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    dataset_version = request_payload["dataset_version"]
    dataset_dir = PROJECT_ROOT / "datasets" / dataset_version
    data_yaml = dataset_dir / "data.yaml"
    output_dir = PROJECT_ROOT / "models" / "candidates" / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "train.log"
    if request_payload.get("dry_run"):
        log_path.write_text("dry_run=true: treino nao executado.\n", encoding="utf-8")
        job.update({"status": "completed", "finished_at": datetime.now(timezone.utc).isoformat(), "output_dir": str(output_dir.relative_to(PROJECT_ROOT)), "log_path": str(log_path.relative_to(PROJECT_ROOT))})
        return
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "ml_training" / "train_candidate.py"),
        "--data",
        str(data_yaml),
        "--model",
        request_payload["model"],
        "--epochs",
        str(request_payload["epochs"]),
        "--imgsz",
        str(request_payload["imgsz"]),
        "--batch",
        str(request_payload["batch"]),
        "--device",
        request_payload["device"],
        "--project",
        str(output_dir),
    ]
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, stdout=log_file, stderr=subprocess.STDOUT, check=False)
        job["status"] = "completed" if result.returncode == 0 else "failed"
        job["returncode"] = result.returncode
    except Exception as exc:  # pragma: no cover - defensive job boundary
        log_path.write_text(str(exc), encoding="utf-8")
        job["status"] = "failed"
        job["error"] = str(exc)
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    job["output_dir"] = str(output_dir.relative_to(PROJECT_ROOT))
    job["log_path"] = str(log_path.relative_to(PROJECT_ROOT))


@app.get("/annotation/frames")
async def annotation_frames(
    hours: int = Query(12, ge=1, le=24 * 30),
    camera_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    include_empty: bool = True,
    user: dict = Depends(current_operator),
) -> list[dict]:
    require_feature("human_feedback_enabled")
    end_iso = datetime.now(timezone.utc).isoformat()
    start_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    frames = await asyncio.to_thread(list_recent_frames, limit, start_iso, end_iso, camera_id, include_empty)
    allowed = []
    for frame in frames:
        camera = get_camera_from_cache(str(frame.get("camera_id") or ""))
        if can_user_view_camera_frame(user, camera):
            frame["image_url"] = f"/frames/{frame['id']}/image" if frame.get("has_image") else ""
            allowed.append(frame)
    return allowed


@app.post("/annotation/frames/{frame_id}/boxes")
async def annotation_frame_boxes(frame_id: int, payload: FrameAnnotationRequest, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("human_feedback_enabled")
    frame = await asyncio.to_thread(get_frame, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="frame not found")
    camera_id = str(frame.get("camera_id") or (frame.get("camera") or {}).get("id") or "")
    camera = get_camera_from_cache(camera_id)
    if not can_user_view_camera_frame(user, camera):
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    width = int((frame.get("frame") or {}).get("width") or 0)
    height = int((frame.get("frame") or {}).get("height") or 0)
    boxes = []
    for item in payload.boxes:
        box = clamp_bbox(item.bbox.model_dump(), width, height)
        if box:
            boxes.append({"object_type": item.object_type, "bbox": box, "confidence": item.confidence, "comment": item.comment, "source": "annotation_tool"})
    if not boxes:
        raise HTTPException(status_code=400, detail="no valid boxes")
    feedback_payload = {
        "feedback_type": "false_negative",
        "frame_id": frame_id,
        "camera_id": camera_id,
        "track_id": "",
        "original_value": "",
        "corrected_value": ",".join(sorted({item["object_type"] for item in boxes})),
        "confidence": max(int(item.get("confidence") or 0) for item in boxes),
        "comment": payload.comment,
        "curation_status": payload.curation_status,
        "payload": {"source": "annotation_tool", "annotation_boxes": boxes, "image_width": width, "image_height": height},
    }
    item = await asyncio.to_thread(create_feedback_annotation, None, feedback_payload, user)
    await audit(user, "annotation.boxes_create", f"frame:{frame_id}", {"feedback_id": item.get("id"), "boxes": len(boxes)}, request)
    return item


@app.post("/annotation/export-yolo")
async def annotation_export_yolo(payload: YoloDatasetExportRequest, request: Request, user: dict = Depends(current_admin)) -> dict:
    require_feature("dataset_export_enabled")
    require_feature("human_feedback_enabled")
    export = await asyncio.to_thread(write_yolo_dataset, payload, user)
    await audit(user, "annotation.export_yolo", export["dataset_version"], export, request)
    return export


@app.post("/annotation/train-yolo")
async def annotation_train_yolo(payload: YoloTrainingRequest, request: Request, user: dict = Depends(current_admin)) -> dict:
    require_feature("dataset_export_enabled")
    dataset_dir = PROJECT_ROOT / "datasets" / payload.dataset_version
    if not (dataset_dir / "data.yaml").exists():
        raise HTTPException(status_code=404, detail="dataset not found")
    job_id = f"train_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    training_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user.get("username", ""),
        "request": payload.model_dump(),
    }
    thread = threading.Thread(target=run_training_job, args=(job_id, payload.model_dump()), daemon=True)
    thread.start()
    await audit(user, "annotation.train_yolo", job_id, payload.model_dump(), request)
    return training_jobs[job_id]


@app.get("/annotation/train-yolo/{job_id}")
async def annotation_training_job(job_id: str, user: dict = Depends(current_admin)) -> dict:
    job = training_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/feedback/export-dataset")
async def feedback_export_dataset(payload: FeedbackExportRequest, request: Request, user: dict = Depends(current_admin)) -> dict:
    require_feature("dataset_export_enabled")
    require_feature("human_feedback_enabled")
    start_iso = normalize_event_timestamp(payload.start) if payload.start else None
    end_iso = normalize_event_timestamp(payload.end) if payload.end else None
    rows = await asyncio.to_thread(
        list_feedback_annotations,
        None,
        start_iso,
        end_iso,
        None,
        None,
        None,
        payload.curation_status,
        False,
        50_000,
    )
    camera_filter = set(payload.camera_id)
    type_filter = set(payload.feedback_type)
    rows = [
        item
        for item in rows
        if (not camera_filter or item.get("camera_id") in camera_filter)
        and (not type_filter or item.get("feedback_type") in type_filter)
        and (payload.include_sensitive or item.get("feedback_type") != "privacy_sensitive")
        and (payload.include_sensitive or item.get("curation_status") != "privacy_blocked")
    ]
    sensitive_event_ids = {
        item.get("event_id")
        for item in await asyncio.to_thread(list_feedback_annotations, None, start_iso, end_iso, None, "privacy_sensitive", None, None, False, 50_000)
        if item.get("event_id")
    }
    if not payload.include_sensitive:
        rows = [item for item in rows if item.get("event_id") not in sensitive_event_ids]

    dataset_version = payload.dataset_version.strip() or f"argos_feedback_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset_version)[:80]
    output_dir = PROJECT_ROOT / "datasets" / safe_version
    output_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for item in rows:
        event = await asyncio.to_thread(get_event, int(item["event_id"])) if item.get("event_id") else None
        frame = await asyncio.to_thread(get_frame, int(item["frame_id"])) if item.get("frame_id") else None
        items.append(
            {
                "feedback": item,
                "event": event,
                "frame": {
                    "id": frame.get("id"),
                    "camera_id": frame.get("camera_id") or (frame.get("camera") or {}).get("id"),
                    "timestamp": frame.get("timestamp"),
                    "image_path": frame.get("image_path", ""),
                } if frame else None,
            }
        )
    metadata = {
        "dataset_version": safe_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user.get("username", ""),
        "privacy_policy": "privacy_sensitive and privacy_blocked items are excluded unless include_sensitive=true",
        "filters": payload.model_dump(),
        "items_count": len(items),
        "items": items,
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    export = await asyncio.to_thread(
        create_feedback_dataset_export,
        safe_version,
        user.get("username", ""),
        payload.model_dump(),
        str(metadata_path.relative_to(PROJECT_ROOT)),
        items,
    )
    await audit(user, "feedback.export_dataset", safe_version, {"items_count": len(items), "path": export["output_path"]}, request)
    return export


@app.patch("/feedback/{feedback_id}")
async def feedback_update(feedback_id: int, payload: FeedbackUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("human_feedback_enabled")
    current = await asyncio.to_thread(get_feedback_annotation, feedback_id)
    if current is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    changes = payload.model_dump(exclude_unset=True)
    item = await asyncio.to_thread(update_feedback_annotation, feedback_id, changes)
    await audit(user, "feedback.update", str(feedback_id), changes, request)
    return item or {}


@app.get("/events/{event_id}")
async def event_by_id(event_id: int, user: dict = Depends(current_user)) -> dict:
    event = await asyncio.to_thread(get_event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@app.get("/geofences")
async def geofences(user: dict = Depends(current_user)) -> list[dict]:
    return await asyncio.to_thread(list_geofences)


@app.post("/geofences")
async def geofence_create(payload: GeoFenceCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    points = [point.model_dump() for point in payload.points]
    validate_polygon_points(points)
    item = await asyncio.to_thread(
        create_geofence,
        {**payload.model_dump(exclude={"points"}), "points": points},
        user.get("username", ""),
    )
    await audit(user, "geofence.create", str(item["id"]), {"name": item["name"], "points": len(item["points"])}, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_updated", "geofence": item})
    return item


@app.patch("/geofences/{geofence_id}")
async def geofence_update(geofence_id: int, payload: GeoFenceUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if "points" in changes:
        changes["points"] = [point.model_dump() for point in payload.points or []]
        validate_polygon_points(changes["points"])
    item = await asyncio.to_thread(update_geofence, geofence_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="geofence not found")
    await audit(user, "geofence.update", str(geofence_id), changes, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_updated", "geofence": item})
    return item


@app.delete("/geofences/{geofence_id}")
async def geofence_delete(geofence_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    deleted = await asyncio.to_thread(delete_geofence, geofence_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="geofence not found")
    await audit(user, "geofence.delete", str(geofence_id), {}, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_deleted", "geofence_id": geofence_id})
    return {"deleted": True}


@app.get("/geofence-rules")
async def geofence_rules(user: dict = Depends(current_user)) -> list[dict]:
    return await asyncio.to_thread(list_geofence_rules)


@app.get("/api/geofences/{geofence_id}/rules")
async def api_geofence_rules(geofence_id: int, user: dict = Depends(current_user)) -> list[dict]:
    rules = await asyncio.to_thread(list_geofence_rules)
    return [rule for rule in rules if int(rule.get("geofence_id") or 0) == geofence_id]


@app.post("/geofence-rules")
async def geofence_rule_create(payload: GeoFenceRuleCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    validate_daily_time(payload.start_time)
    validate_daily_time(payload.end_time)
    validate_daily_time(payload.active_time_start)
    validate_daily_time(payload.active_time_end)
    item = await asyncio.to_thread(create_geofence_rule, payload.model_dump(), user.get("username", ""))
    await audit(user, "geofence_rule.create", str(item["id"]), item, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_rule_updated", "rule": item})
    return item


@app.post("/api/geofences/{geofence_id}/rules")
async def api_geofence_rule_create(geofence_id: int, payload: GeoFenceRuleCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    return await geofence_rule_create(payload.model_copy(update={"geofence_id": geofence_id}), request, user)


@app.patch("/geofence-rules/{rule_id}")
async def geofence_rule_update(rule_id: int, payload: GeoFenceRuleUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if "start_time" in changes:
        validate_daily_time(changes["start_time"])
    if "end_time" in changes:
        validate_daily_time(changes["end_time"])
    if "active_time_start" in changes:
        validate_daily_time(changes["active_time_start"])
    if "active_time_end" in changes:
        validate_daily_time(changes["active_time_end"])
    item = await asyncio.to_thread(update_geofence_rule, rule_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="geofence rule not found")
    await audit(user, "geofence_rule.update", str(rule_id), changes, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_rule_updated", "rule": item})
    return item


@app.put("/api/geofence-rules/{rule_id}")
async def api_geofence_rule_put(rule_id: int, payload: GeoFenceRuleUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    return await geofence_rule_update(rule_id, payload, request, user)


@app.delete("/geofence-rules/{rule_id}")
async def geofence_rule_delete(rule_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    deleted = await asyncio.to_thread(delete_geofence_rule, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="geofence rule not found")
    await audit(user, "geofence_rule.delete", str(rule_id), {}, request)
    invalidate_geofence_cache()
    await broadcast({"event": "geofence_rule_deleted", "rule_id": rule_id})
    return {"deleted": True}


@app.delete("/api/geofence-rules/{rule_id}")
async def api_geofence_rule_delete(rule_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    return await geofence_rule_delete(rule_id, request, user)


@app.get("/geofence-alerts")
async def geofence_alerts(
    limit: int = 100,
    include_resolved: bool = False,
    user: dict = Depends(current_user),
) -> list[dict]:
    return await asyncio.to_thread(list_geofence_alerts, min(max(limit, 1), 500), include_resolved)


@app.patch("/geofence-alerts/{alert_id}")
async def geofence_alert_update(alert_id: int, payload: GeoFenceAlertUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    current = await asyncio.to_thread(get_geofence_alert, alert_id)
    if current is None:
        raise HTTPException(status_code=404, detail="geofence alert not found")
    changes = payload.model_dump(exclude_unset=True)
    if "status" not in changes:
        changes["status"] = "resolved"
    if changes["status"] in {"resolved", "dismissed"} and not changes.get("resolution"):
        changes["resolution"] = "alerta de geofence encerrado"
    try:
        item = await asyncio.to_thread(update_geofence_alert, alert_id, changes, user.get("username", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    incident_id = item.get("incident_id") if item else None
    if incident_id and item.get("status") in {"resolved", "dismissed"}:
        incident = await asyncio.to_thread(
            update_incident,
            int(incident_id),
            {"status": "closed", "resolution": item.get("resolution") or "alerta de geofence encerrado"},
            user.get("username", ""),
        )
        if incident:
            await asyncio.to_thread(
                add_incident_timeline,
                int(incident_id),
                "incident.closed",
                item.get("resolution") or "Alerta de geofence encerrado.",
                user.get("username", ""),
                {"geofence_alert_id": alert_id, "status": item.get("status")},
            )
            await asyncio.to_thread(
                upsert_attention_item_state,
                f"incident:{incident_id}",
                {"status": "resolved", "resolution": item.get("resolution") or "alerta de geofence encerrado"},
                user.get("username", ""),
            )
            await broadcast({"event": "incident_updated", "incident": incident})
    await audit(user, "geofence_alert.update", str(alert_id), {"status": item.get("status"), "resolution": item.get("resolution")}, request)
    await broadcast({"event": "geofence_alert_updated", "alert": item})
    await broadcast({"event": "attention_updated", "item_key": f"geofence_alert:{alert_id}", "state": item})
    return item


@app.get("/api/tracks/active")
async def api_active_tracks(limit: int = 250, user: dict = Depends(current_user)) -> list[dict]:
    memory_tracks = temporal_tracker.active_tracks()
    if memory_tracks:
        return memory_tracks[: min(max(limit, 1), 1000)]
    return await asyncio.to_thread(list_object_tracks, True, min(max(limit, 1), 1000))


@app.get("/api/events")
async def api_temporal_events(limit: int = 100, status: str | None = None, user: dict = Depends(current_user)) -> list[dict]:
    from db import list_temporal_events

    return await asyncio.to_thread(list_temporal_events, min(max(limit, 1), 500), status)


@app.post("/api/events/{event_id}/acknowledge")
async def api_temporal_event_acknowledge(event_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    item = await asyncio.to_thread(acknowledge_temporal_event, event_id, user.get("username", ""))
    if item is None:
        raise HTTPException(status_code=404, detail="event not found")
    await audit(user, "temporal_event.acknowledge", str(event_id), {"track_id": item.get("track_id")}, request)
    return item


SEVERITY_SCORES = {"low": 20, "medium": 45, "high": 70, "critical": 90}
ATTENTION_QUICK_ACTIONS = {
    "confirm_risk": {
        "status": "assigned",
        "resolution": "risco confirmado",
        "label": "Risco confirmado",
    },
    "false_alarm": {
        "status": "resolved",
        "resolution": "falso alarme",
        "label": "Falso alarme",
    },
    "monitor": {
        "status": "assigned",
        "resolution": "monitorar",
        "label": "Monitorar",
    },
    "notify_responsible": {
        "status": "assigned",
        "resolution": "responsavel acionado",
        "label": "Responsavel acionado",
    },
    "ignore": {
        "status": "dismissed",
        "resolution": "ignorado com motivo",
        "label": "Ignorado",
    },
}


def parse_attention_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def attention_age_minutes(timestamp: str | None) -> int:
    return max(0, int((datetime.now(timezone.utc) - parse_attention_time(timestamp)).total_seconds() // 60))


def merge_attention_state(item: dict, states: dict[str, dict]) -> dict:
    state = states.get(item["item_key"]) or {
        "status": item.get("status") or "open",
        "assigned_to": item.get("assigned_to") or "",
        "assigned_at": item.get("assigned_at") or "",
        "resolved_at": "",
        "resolution": "",
        "comment": "",
        "quick_action": "",
        "confirmed_at": "",
    }
    item["state"] = state
    item["status"] = state.get("status") or "open"
    item["assigned_to"] = state.get("assigned_to") or ""
    item["assigned_at"] = state.get("assigned_at") or ""
    item["resolved_at"] = state.get("resolved_at") or ""
    item["resolution"] = state.get("resolution") or ""
    item["state_comment"] = state.get("comment") or ""
    item["quick_action"] = state.get("quick_action") or ""
    item["confirmed_at"] = state.get("confirmed_at") or ""
    item["quick_action_label"] = ATTENTION_QUICK_ACTIONS.get(item["quick_action"], {}).get("label", "")
    return item


def build_temporal_attention_item(event: dict) -> dict:
    camera = get_camera_from_cache(event.get("camera_id") or "") or {}
    severity = event.get("severity") or "medium"
    age = attention_age_minutes(event.get("created_at"))
    duration = max(int(event.get("stationary_duration_seconds") or 0), int(event.get("geofence_duration_seconds") or 0), int(event.get("seen_duration_seconds") or 0))
    score = SEVERITY_SCORES.get(severity, 45) + min(20, age // 5) + min(20, duration // 120) + int(camera.get("priority_level") or 3)
    return {
        "item_key": f"temporal_event:{event['id']}",
        "kind": "temporal_event",
        "title": event.get("message") or "Evento temporal exige atenção",
        "summary": f"{event.get('object_type') or 'Objeto'} em {camera.get('name') or event.get('camera_id') or '-'}",
        "severity": severity,
        "score": min(100, score),
        "created_at": event.get("created_at"),
        "camera_id": event.get("camera_id") or "",
        "camera_name": camera.get("name") or event.get("camera_id") or "",
        "event_id": event.get("id"),
        "frame_id": event.get("frame_reference"),
        "track_id": event.get("track_id") or "",
        "object_type": event.get("object_type") or "",
        "action_hint": "Verifique o frame, confirme risco ou assuma atendimento.",
        "source": event,
    }


def build_camera_attention_item(camera: dict, capture_status: dict) -> dict | None:
    camera_id = camera.get("id") or ""
    if not camera_id:
        return None
    status = "inactive" if not camera.get("active", True) else ""
    detail = ""
    if not status:
        item = (capture_status.get("cameras") or {}).get(camera_id) or {}
        if item and not item.get("ok", True):
            status = "offline"
            detail = item.get("error") or "captura sem frame valido"
        elif item and float(item.get("age_s") or 0) > float(capture_status.get("max_stale_s") or CAPTURE_WORKER_MAX_STALE_S):
            status = "stale"
            detail = f"frame stale ha {item.get('age_s')}s"
    if not status:
        return None
    severity = "medium" if status == "inactive" else "high"
    score = (40 if status == "inactive" else 68) + int(camera.get("priority_level") or 3) * 2
    return {
        "item_key": f"camera:{camera_id}:{status}",
        "kind": "camera_status",
        "title": "Câmera pausada" if status == "inactive" else "Câmera com falha de captura",
        "summary": f"{camera.get('name') or camera_id} · {detail or status}",
        "severity": severity,
        "score": min(100, score),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "camera_id": camera_id,
        "camera_name": camera.get("name") or camera_id,
        "event_id": None,
        "frame_id": None,
        "track_id": "",
        "object_type": "",
        "action_hint": "Verifique conexão, fonte de vídeo ou reative a câmera.",
        "source": {"camera": camera, "capture_status": (capture_status.get("cameras") or {}).get(camera_id) or {}, "status": status},
    }


def build_incident_attention_item(incident: dict) -> dict:
    severity = incident.get("severity") or "medium"
    age = attention_age_minutes(incident.get("opened_at"))
    camera = get_camera_from_cache(incident.get("camera_id") or "") or {}
    score = SEVERITY_SCORES.get(severity, 45) + min(25, age // 3) + int(camera.get("priority_level") or 3)
    return {
        "item_key": f"incident:{incident['id']}",
        "kind": "incident",
        "title": incident.get("title") or "Ocorrencia aberta",
        "summary": incident.get("summary") or f"Ocorrencia #{incident['id']}",
        "severity": severity,
        "score": min(100, score),
        "created_at": incident.get("opened_at"),
        "camera_id": incident.get("camera_id") or "",
        "camera_name": camera.get("name") or incident.get("camera_id") or "",
        "event_id": incident.get("event_id"),
        "frame_id": incident.get("frame_id"),
        "track_id": incident.get("track_id") or "",
        "object_type": incident.get("object_type") or "",
        "status": "assigned" if incident.get("assigned_to") else "open",
        "assigned_to": incident.get("assigned_to") or "",
        "assigned_at": incident.get("opened_at") if incident.get("assigned_to") else "",
        "action_hint": "Acompanhe a ocorrencia no mapa ou resolva quando o atendimento terminar.",
        "source": incident,
    }


def geofence_alert_id_from_incident(incident: dict | None) -> int | None:
    if not incident:
        return None
    candidates: list[object] = []
    source_key = str(incident.get("source_key") or "")
    if incident.get("source_kind") == "geofence_alert" and source_key:
        match = re.search(r"geofence_alert:(\d+)", source_key)
        if match:
            candidates.append(match.group(1))
    metadata = incident.get("metadata") or {}
    attention_item = metadata.get("attention_item") if isinstance(metadata, dict) else {}
    attention_source = attention_item.get("source") if isinstance(attention_item, dict) else {}
    candidates.extend(
        [
            metadata.get("geofence_alert_id") if isinstance(metadata, dict) else None,
            (metadata.get("alert") or {}).get("id") if isinstance(metadata.get("alert"), dict) else None,
            attention_source.get("id") if isinstance(attention_source, dict) else None,
        ]
    )
    for value in candidates:
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def build_attention_queue(include_resolved: bool = False, limit: int = 50) -> dict:
    temporal_events = list_temporal_events(200, None)
    items = [
        build_temporal_attention_item(event)
        for event in temporal_events
        if event.get("status") not in {"closed", "resolved", "dismissed"}
    ]
    items.extend(build_incident_attention_item(incident) for incident in list_incidents("open", 100))
    capture_status = capture_manager.snapshot_status()
    for camera in CAMERAS:
        camera_item = build_camera_attention_item(camera, capture_status)
        if camera_item:
            items.append(camera_item)
    states = list_attention_item_states([item["item_key"] for item in items])
    output = [merge_attention_state(item, states) for item in items]
    if not include_resolved:
        output = [item for item in output if item.get("status") not in {"resolved", "dismissed"}]
    output.sort(key=lambda item: (item.get("status") != "open", -float(item.get("score") or 0), item.get("created_at") or ""), reverse=False)
    summary = {
        "total": len(output),
        "open": sum(1 for item in output if item.get("status") == "open"),
        "assigned": sum(1 for item in output if item.get("status") == "assigned"),
        "critical": sum(1 for item in output if item.get("severity") == "critical"),
        "high": sum(1 for item in output if item.get("severity") == "high"),
    }
    return {"items": output[:limit], "summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}


def find_attention_item(item_key: str) -> dict | None:
    queue = build_attention_queue(include_resolved=True, limit=500)
    return next((item for item in queue.get("items", []) if item.get("item_key") == item_key), None)


def pdf_escape(value: object) -> str:
    return str(value if value is not None else "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_simple_pdf(title: str, lines: list[str]) -> bytes:
    page_width = 595
    page_height = 842
    y = 800
    content = ["BT", "/F1 16 Tf", f"50 {y} Td", f"({pdf_escape(title)}) Tj"]
    y -= 28
    content.extend(["/F1 10 Tf", f"0 -28 Td"])
    for raw_line in lines:
        line = str(raw_line)
        chunks = [line[index : index + 92] for index in range(0, max(len(line), 1), 92)] or [""]
        for chunk in chunks:
            if y < 54:
                content.append("(...) Tj")
                content.append("ET")
                return assemble_pdf(page_width, page_height, "\n".join(content).encode("latin-1", "replace"))
            content.append(f"({pdf_escape(chunk)}) Tj")
            content.append("0 -14 Td")
            y -= 14
    content.append("ET")
    return assemble_pdf(page_width, page_height, "\n".join(content).encode("latin-1", "replace"))


def assemble_pdf(page_width: int, page_height: int, stream: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


def incident_report_pdf(incident: dict, timeline: list[dict]) -> bytes:
    metadata = incident.get("metadata") or {}
    geo = metadata.get("geo") or (metadata.get("detection") or {}).get("geo") or {}
    opened = parse_attention_time(incident.get("opened_at"))
    closed_at = incident.get("closed_at") or ""
    closed = parse_attention_time(closed_at) if closed_at else None
    duration = str(closed - opened).split(".")[0] if closed else "em aberto"
    lines = [
        f"ID: {incident.get('id')}",
        f"Titulo: {incident.get('title')}",
        f"Status: {incident.get('status')} | Grau: {incident.get('severity')}",
        f"Resumo/descricao: {incident.get('summary') or '-'}",
        f"Aberta em: {incident.get('opened_at')} por {incident.get('opened_by') or '-'}",
        f"Responsavel: {incident.get('assigned_to') or '-'}",
        f"Encerrada em: {incident.get('closed_at') or '-'} por {incident.get('closed_by') or '-'}",
        f"Tempo de resolucao: {duration}",
        f"Resolucao: {incident.get('resolution') or '-'}",
        f"Origem: {incident.get('source_kind') or '-'} / {incident.get('source_key') or '-'}",
        f"Camera: {incident.get('camera_id') or '-'} | Frame: {incident.get('frame_id') or '-'} | Evento: {incident.get('event_id') or '-'}",
        f"Objeto/track: {incident.get('object_type') or '-'} / {incident.get('track_id') or '-'}",
        f"Localizacao: lat {geo.get('lat', '-')} lon {geo.get('lon', '-')}",
        "",
        "Linha do tempo:",
    ]
    for item in timeline:
        lines.append(f"- {item.get('timestamp')} | {item.get('actor') or 'sistema'} | {item.get('action')} | {item.get('message') or '-'}")
    lines.extend(["", "Metadados/evidencias:", json.dumps(metadata, ensure_ascii=False, default=str)[:4000]])
    return build_simple_pdf(f"Relatorio da Ocorrencia #{incident.get('id')}", lines)


@app.get("/api/attention")
async def api_attention_queue(
    include_resolved: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(current_user),
) -> dict:
    return await asyncio.to_thread(build_attention_queue, include_resolved, limit)


@app.patch("/api/attention/{item_key:path}")
async def api_attention_update(item_key: str, payload: AttentionItemUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    changes = payload.model_dump()
    if payload.status == "assigned":
        changes["assigned_to"] = user.get("username", "")
    if item_key.startswith("incident:"):
        try:
            incident_id = int(item_key.split(":", 1)[1])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid incident attention key") from exc
        incident_changes: dict[str, object] = {}
        if payload.status == "assigned":
            incident_changes["assigned_to"] = user.get("username", "")
        if payload.status in {"resolved", "dismissed"}:
            incident_changes["status"] = "closed"
            incident_changes["resolution"] = payload.resolution or ("ignorado pela fila" if payload.status == "dismissed" else "resolvido pela fila")
        if incident_changes:
            incident = await asyncio.to_thread(update_incident, incident_id, incident_changes, user.get("username", ""))
            if incident is None:
                raise HTTPException(status_code=404, detail="incident not found")
            await asyncio.to_thread(
                add_incident_timeline,
                incident_id,
                "incident.closed" if incident_changes.get("status") == "closed" else "incident.updated",
                str(incident_changes.get("resolution") or "Ocorrencia assumida pela fila."),
                user.get("username", ""),
                incident_changes,
            )
            await broadcast({"event": "incident_updated", "incident": incident})
    item = await asyncio.to_thread(upsert_attention_item_state, item_key, changes, user.get("username", ""))
    await audit(user, "attention.update", item_key, item, request)
    await broadcast({"event": "attention_updated", "item_key": item_key, "state": item})
    return item


@app.post("/api/attention/{item_key:path}/confirm")
async def api_attention_quick_confirm(item_key: str, payload: AttentionQuickConfirm, request: Request, user: dict = Depends(current_operator)) -> dict:
    action = ATTENTION_QUICK_ACTIONS[payload.action]
    changes = {
        "status": action["status"],
        "resolution": action["resolution"],
        "quick_action": payload.action,
        "comment": payload.comment,
    }
    if action["status"] == "assigned":
        changes["assigned_to"] = user.get("username", "")
    item = await asyncio.to_thread(upsert_attention_item_state, item_key, changes, user.get("username", ""))
    await audit(
        user,
        "attention.quick_confirm",
        item_key,
        {"action": payload.action, "label": action["label"], "state": item},
        request,
    )
    await broadcast({"event": "attention_updated", "item_key": item_key, "state": item})
    return {"item_key": item_key, "action": payload.action, "label": action["label"], "state": item}


@app.get("/api/incidents")
async def api_incidents(status: str | None = "open", limit: int = Query(50, ge=1, le=200), user: dict = Depends(current_user)) -> list[dict]:
    return await asyncio.to_thread(list_incidents, status, limit)


@app.post("/api/incidents")
async def api_incident_create(payload: IncidentCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    data = payload.model_dump()
    title = data.pop("title", "").strip() or "Ocorrencia manual"
    summary = data.pop("summary", "").strip()
    metadata = data.pop("metadata", {}) or {}
    incident_payload = {
        **data,
        "title": title,
        "summary": summary,
        "status": "open",
        "assigned_to": user.get("username", ""),
        "opened_by": user.get("username", ""),
        "metadata": metadata,
        "timeline_message": "Ocorrencia aberta manualmente a partir do mapa.",
        "timeline_payload": {
            "source_kind": payload.source_kind,
            "source_key": payload.source_key,
            "frame_id": payload.frame_id,
            "camera_id": payload.camera_id,
            "metadata": metadata,
        },
    }
    incident = await asyncio.to_thread(create_incident, incident_payload, user.get("username", ""))
    geofence_alert_id = geofence_alert_id_from_incident(incident)
    if geofence_alert_id:
        await asyncio.to_thread(update_geofence_alert, geofence_alert_id, {"incident_id": incident.get("id")}, user.get("username", ""))
    await audit(user, "incident.create_manual", str(incident.get("id")), incident_payload, request)
    await broadcast({"event": "incident_created", "incident": incident})
    return {**incident, "timeline": await asyncio.to_thread(list_incident_timeline, int(incident["id"]))}


@app.post("/api/attention/{item_key:path}/incident")
async def api_incident_from_attention(item_key: str, payload: IncidentFromAttentionRequest, request: Request, user: dict = Depends(current_operator)) -> dict:
    existing = await asyncio.to_thread(get_incident_by_attention_key, item_key, True)
    if existing:
        return {**existing, "timeline": await asyncio.to_thread(list_incident_timeline, int(existing["id"]))}
    attention_item = await asyncio.to_thread(find_attention_item, item_key)
    if attention_item is None:
        raise HTTPException(status_code=404, detail="attention item not found")
    title = payload.title.strip() or attention_item.get("title") or "Ocorrencia"
    summary = payload.summary.strip() or attention_item.get("summary") or ""
    incident_payload = {
        "title": title,
        "summary": summary,
        "status": "open",
        "severity": attention_item.get("severity") or "medium",
        "source_kind": attention_item.get("kind") or "",
        "source_key": item_key,
        "attention_item_key": item_key,
        "camera_id": attention_item.get("camera_id") or "",
        "event_id": attention_item.get("event_id"),
        "frame_id": attention_item.get("frame_id"),
        "track_id": attention_item.get("track_id") or "",
        "object_type": attention_item.get("object_type") or "",
        "assigned_to": user.get("username", ""),
        "metadata": {"attention_item": attention_item},
        "timeline_message": "Ocorrencia aberta a partir da Fila de Atencao.",
        "timeline_payload": {"attention_item_key": item_key},
    }
    incident = await asyncio.to_thread(create_incident, incident_payload, user.get("username", ""))
    state = await asyncio.to_thread(
        upsert_attention_item_state,
        item_key,
        {"status": "assigned", "assigned_to": user.get("username", ""), "resolution": "ocorrencia aberta"},
        user.get("username", ""),
    )
    await audit(user, "incident.create", str(incident.get("id")), {"attention_item_key": item_key, "state": state}, request)
    await broadcast({"event": "incident_created", "incident": incident})
    await broadcast({"event": "attention_updated", "item_key": item_key, "state": state})
    return {**incident, "timeline": await asyncio.to_thread(list_incident_timeline, int(incident["id"]))}


@app.get("/api/incidents/{incident_id}")
async def api_incident_detail(incident_id: int, user: dict = Depends(current_user)) -> dict:
    incident = await asyncio.to_thread(get_incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    timeline = await asyncio.to_thread(list_incident_timeline, incident_id)
    return {**incident, "timeline": timeline}


@app.get("/api/incidents/{incident_id}/report.pdf")
async def api_incident_report_pdf(incident_id: int, user: dict = Depends(current_user)) -> Response:
    incident = await asyncio.to_thread(get_incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    timeline = await asyncio.to_thread(list_incident_timeline, incident_id)
    pdf = await asyncio.to_thread(incident_report_pdf, incident, timeline)
    filename = f"ocorrencia-{incident_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/incidents/{incident_id}/timeline")
async def api_incident_timeline_add(incident_id: int, payload: IncidentTimelineCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    incident = await asyncio.to_thread(get_incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    item = await asyncio.to_thread(add_incident_timeline, incident_id, payload.action, payload.message, user.get("username", ""), payload.payload)
    await audit(user, "incident.timeline", str(incident_id), {"action": payload.action}, request)
    await broadcast({"event": "incident_updated", "incident_id": incident_id})
    return item


@app.patch("/api/incidents/{incident_id}")
async def api_incident_update(incident_id: int, payload: IncidentUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    current = await asyncio.to_thread(get_incident, incident_id)
    if current is None:
        raise HTTPException(status_code=404, detail="incident not found")
    changes = payload.model_dump(exclude_unset=True)
    incident = await asyncio.to_thread(update_incident, incident_id, changes, user.get("username", ""))
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    action = "incident.closed" if changes.get("status") == "closed" else "incident.updated"
    message = changes.get("resolution") or "Ocorrencia atualizada."
    await asyncio.to_thread(add_incident_timeline, incident_id, action, message, user.get("username", ""), changes)
    if changes.get("status") == "closed" and current.get("attention_item_key"):
        await asyncio.to_thread(
            upsert_attention_item_state,
            current["attention_item_key"],
            {"status": "resolved", "resolution": changes.get("resolution") or "ocorrencia encerrada"},
            user.get("username", ""),
        )
    if changes.get("status") == "closed":
        geofence_alert_id = geofence_alert_id_from_incident(current) or geofence_alert_id_from_incident(incident)
        if geofence_alert_id:
            try:
                alert = await asyncio.to_thread(
                    update_geofence_alert,
                    geofence_alert_id,
                    {
                        "status": "resolved",
                        "resolution": changes.get("resolution") or "ocorrencia encerrada",
                        "incident_id": incident_id,
                    },
                    user.get("username", ""),
                )
            except ValueError:
                alert = None
            if alert:
                await broadcast({"event": "geofence_alert_updated", "alert": alert})
    await audit(user, "incident.update", str(incident_id), changes, request)
    await broadcast({"event": "incident_updated", "incident": incident})
    return {**incident, "timeline": await asyncio.to_thread(list_incident_timeline, incident_id)}


@app.get("/api/floor-plans")
async def floor_plans(user: dict = Depends(current_user)) -> list[dict]:
    require_feature("floor_plans_enabled")
    return await asyncio.to_thread(list_floor_plans)


@app.post("/api/floor-plans")
async def floor_plan_create(payload: FloorPlanCreate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    item = await asyncio.to_thread(create_floor_plan, payload.model_dump(), user.get("username", ""))
    await audit(user, "floor_plan.create", str(item["id"]), {"name": item["name"]}, request)
    return item


@app.get("/api/floor-plans/{floor_plan_id}")
async def floor_plan_detail(floor_plan_id: int, user: dict = Depends(current_user)) -> dict:
    require_feature("floor_plans_enabled")
    item = await asyncio.to_thread(get_floor_plan, floor_plan_id)
    if item is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    item["cameras"] = await asyncio.to_thread(list_floor_plan_cameras, floor_plan_id)
    item["zones"] = await asyncio.to_thread(list_floor_plan_zones, floor_plan_id)
    return item


@app.put("/api/floor-plans/{floor_plan_id}")
async def floor_plan_update(floor_plan_id: int, payload: FloorPlanUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    changes = payload.model_dump(exclude_unset=True)
    item = await asyncio.to_thread(update_floor_plan, floor_plan_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    await audit(user, "floor_plan.update", str(floor_plan_id), changes, request)
    return item


@app.delete("/api/floor-plans/{floor_plan_id}")
async def floor_plan_delete(floor_plan_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    deleted = await asyncio.to_thread(delete_floor_plan, floor_plan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="floor plan not found")
    shutil.rmtree(FLOOR_PLAN_STATIC_DIR / str(floor_plan_id), ignore_errors=True)
    await audit(user, "floor_plan.delete", str(floor_plan_id), {}, request)
    return {"deleted": True}


def detect_floor_plan_image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    raise HTTPException(status_code=400, detail="floor plan image must be PNG or JPG")


async def read_limited_upload(file: UploadFile, max_bytes: int) -> bytes:
    chunks = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise HTTPException(status_code=413, detail=f"floor plan image exceeds {max_bytes} bytes")
    if not chunks:
        raise HTTPException(status_code=400, detail="empty upload")
    return bytes(chunks)


def decode_floor_plan_image(data: bytes) -> tuple[int, int]:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="invalid image")
    height, width = image.shape[:2]
    pixels = width * height
    if width > FLOOR_PLAN_MAX_SIDE_PX or height > FLOOR_PLAN_MAX_SIDE_PX or pixels > FLOOR_PLAN_MAX_PIXELS:
        raise HTTPException(status_code=413, detail="floor plan image dimensions exceed configured limits")
    return width, height


def write_floor_plan_image(target_path: Path, data: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target_path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(data)
    try:
        temp_path.replace(target_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@app.post("/api/floor-plans/{floor_plan_id}/upload")
async def floor_plan_upload(
    floor_plan_id: int,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("floor_plans_enabled")
    floor_plan = await asyncio.to_thread(get_floor_plan, floor_plan_id)
    if floor_plan is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    content_type = (file.content_type or "").lower()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"} and content_type not in {"image/png", "image/jpeg"}:
        raise HTTPException(status_code=400, detail="floor plan image must be PNG or JPG")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > FLOOR_PLAN_UPLOAD_MAX_BYTES + 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"floor plan image exceeds {FLOOR_PLAN_UPLOAD_MAX_BYTES} bytes")
        except ValueError:
            pass
    data = await read_limited_upload(file, FLOOR_PLAN_UPLOAD_MAX_BYTES)
    detected_content_type, suffix = detect_floor_plan_image_type(data)
    if content_type and content_type not in {detected_content_type, "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="floor plan image content type does not match file data")
    width, height = decode_floor_plan_image(data)
    target_dir = FLOOR_PLAN_STATIC_DIR / str(floor_plan_id)
    target_path = target_dir / f"plan{suffix}"
    await asyncio.to_thread(write_floor_plan_image, target_path, data)
    image_path = f"/static/floor_plans/{floor_plan_id}/plan{suffix}"
    item = await asyncio.to_thread(
        update_floor_plan,
        floor_plan_id,
        {"image_path": image_path, "image_width_px": width, "image_height_px": height},
    )
    await audit(user, "floor_plan.upload", str(floor_plan_id), {"width": width, "height": height}, request)
    return item


@app.get("/api/floor-plans/{floor_plan_id}/cameras")
async def floor_plan_cameras(floor_plan_id: int, user: dict = Depends(current_user)) -> list[dict]:
    require_feature("floor_plans_enabled")
    if await asyncio.to_thread(get_floor_plan, floor_plan_id) is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    return await asyncio.to_thread(list_floor_plan_cameras, floor_plan_id)


@app.get("/api/floor-plans/{floor_plan_id}/zones")
async def floor_plan_zones(floor_plan_id: int, user: dict = Depends(current_user)) -> list[dict]:
    require_feature("floor_plans_enabled")
    if await asyncio.to_thread(get_floor_plan, floor_plan_id) is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    return await asyncio.to_thread(list_floor_plan_zones, floor_plan_id)


@app.post("/api/floor-plans/{floor_plan_id}/zones")
async def floor_plan_zone_create(
    floor_plan_id: int,
    payload: FloorPlanZoneCreate,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("floor_plans_enabled")
    plan = await asyncio.to_thread(get_floor_plan, floor_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    polygon = [point.model_dump() for point in payload.polygon]
    validate_floor_polygon(plan, polygon)
    item = await asyncio.to_thread(
        create_floor_plan_zone,
        floor_plan_id,
        {**payload.model_dump(exclude={"polygon"}), "polygon": polygon},
        user.get("username", ""),
    )
    await audit(user, "floor_plan_zone.create", str(item["id"]), {"floor_plan_id": floor_plan_id, "name": item["name"]}, request)
    return item


@app.put("/api/floor-plan-zones/{zone_id}")
async def floor_plan_zone_update(zone_id: int, payload: FloorPlanZoneUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    current = await asyncio.to_thread(get_floor_plan_zone, zone_id)
    if current is None:
        raise HTTPException(status_code=404, detail="floor plan zone not found")
    changes = payload.model_dump(exclude_unset=True)
    if "polygon" in changes:
        polygon = [point.model_dump() for point in payload.polygon or []]
        plan = await asyncio.to_thread(get_floor_plan, current["floor_plan_id"])
        validate_floor_polygon(plan or {}, polygon)
        changes["polygon"] = polygon
    item = await asyncio.to_thread(update_floor_plan_zone, zone_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="floor plan zone not found")
    await audit(user, "floor_plan_zone.update", str(zone_id), changes, request)
    return item


@app.delete("/api/floor-plan-zones/{zone_id}")
async def floor_plan_zone_delete(zone_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    deleted = await asyncio.to_thread(delete_floor_plan_zone, zone_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="floor plan zone not found")
    await audit(user, "floor_plan_zone.delete", str(zone_id), {}, request)
    return {"deleted": True}


@app.post("/api/floor-plans/{floor_plan_id}/cameras")
async def floor_plan_camera_create(
    floor_plan_id: int,
    payload: FloorPlanCameraCreate,
    request: Request,
    user: dict = Depends(current_operator),
) -> dict:
    require_feature("floor_plans_enabled")
    plan = await asyncio.to_thread(get_floor_plan, floor_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    validate_floor_camera_position(plan, payload.x_px, payload.y_px)
    item = await asyncio.to_thread(create_floor_plan_camera, floor_plan_id, payload.model_dump(), user.get("username", ""))
    await audit(user, "floor_plan_camera.create", str(item["id"]), {"floor_plan_id": floor_plan_id, "name": item["name"]}, request)
    return item


@app.put("/api/floor-plan-cameras/{camera_id}")
async def floor_plan_camera_update(camera_id: int, payload: FloorPlanCameraUpdate, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    current = await asyncio.to_thread(get_floor_plan_camera, camera_id)
    if current is None:
        raise HTTPException(status_code=404, detail="floor plan camera not found")
    changes = payload.model_dump(exclude_unset=True)
    if "x_px" in changes or "y_px" in changes:
        plan = await asyncio.to_thread(get_floor_plan, current["floor_plan_id"])
        validate_floor_camera_position(plan or {}, changes.get("x_px", current["x_px"]), changes.get("y_px", current["y_px"]))
    item = await asyncio.to_thread(update_floor_plan_camera, camera_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="floor plan camera not found")
    await audit(user, "floor_plan_camera.update", str(camera_id), changes, request)
    return item


@app.delete("/api/floor-plan-cameras/{camera_id}")
async def floor_plan_camera_delete(camera_id: int, request: Request, user: dict = Depends(current_operator)) -> dict:
    require_feature("floor_plans_enabled")
    deleted = await asyncio.to_thread(delete_floor_plan_camera, camera_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="floor plan camera not found")
    await audit(user, "floor_plan_camera.delete", str(camera_id), {}, request)
    return {"deleted": True}


@app.get("/api/floor-plan-cameras/{camera_id}/processed-frame")
async def floor_plan_camera_processed_frame(camera_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    require_feature("floor_plans_enabled")
    floor_camera = await asyncio.to_thread(get_floor_plan_camera, camera_id)
    if floor_camera is None:
        raise HTTPException(status_code=404, detail="floor plan camera not found")
    if not floor_camera.get("enabled", True):
        return {
            "camera": indoor_processing_camera(floor_camera),
            "detections": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "frame": {"width": 1280, "height": 720, "mode": "camera-paused", "image": ""},
        }
    linked_camera_id = floor_camera.get("linked_camera_id") or ""
    if linked_camera_id:
        camera = get_camera_from_cache(linked_camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="linked camera not found")
        return await load_camera_processed_frame(camera, request, user, refresh=False)
    if not (floor_camera.get("url") or floor_camera.get("source_url")):
        raise HTTPException(status_code=404, detail="floor camera has no stream URL")
    camera = indoor_processing_camera(floor_camera)
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", camera["id"], {"floor_camera_id": camera_id}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    payload = await asyncio.to_thread(build_processed_frame, camera, [], True, None)
    await audit(user, "floor_plan_camera.frame_view", str(camera_id), {"camera_id": camera["id"]}, request)
    return public_frame_payload(payload)


@app.get("/api/floor-plans/{floor_plan_id}/coverage")
async def floor_plan_coverage(floor_plan_id: int, user: dict = Depends(current_user)) -> dict:
    require_feature("floor_plans_enabled")
    plan = await asyncio.to_thread(get_floor_plan, floor_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="floor plan not found")
    cameras = await asyncio.to_thread(list_floor_plan_cameras, floor_plan_id)
    zones = await asyncio.to_thread(list_floor_plan_zones, floor_plan_id)
    return calculate_floor_coverage(plan, cameras, zones)


@app.get("/frames/{frame_id}")
async def frame_by_id(frame_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    frame = await asyncio.to_thread(get_frame, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="frame not found")
    camera_id = frame.get("camera_id") or frame.get("camera", {}).get("id")
    camera = get_camera_from_cache(str(camera_id)) if camera_id else None
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", str(frame_id), {"camera_id": camera_id}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    await audit(user, "frame.view", str(frame_id), {"camera_id": frame.get("camera", {}).get("id")}, request)
    return public_frame_payload(frame)


@app.get("/frames/{frame_id}/metadata")
async def frame_metadata_by_id(frame_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    frame = await asyncio.to_thread(get_frame, frame_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="frame not found")
    camera_id = frame.get("camera_id") or frame.get("camera", {}).get("id")
    camera = get_camera_from_cache(str(camera_id)) if camera_id else None
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", str(frame_id), {"camera_id": camera_id}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    item = public_frame_payload(frame)
    if item.get("frame"):
        item["frame"]["image"] = ""
        item["frame"]["image_url"] = f"/frames/{frame_id}/image"
    return item


@app.get("/frames/{frame_id}/image")
async def frame_image_by_id(frame_id: int, request: Request) -> Response:
    user = get_session(request.cookies.get(SESSION_COOKIE_NAME)) or get_session(parse_bearer_token(request.headers.get("authorization")))
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    image = await asyncio.to_thread(get_frame_image_blob, frame_id)
    if image is None:
        raise HTTPException(status_code=404, detail="frame not found")
    camera_id = image.get("camera_id")
    camera = get_camera_from_cache(str(camera_id)) if camera_id else None
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", str(frame_id), {"camera_id": camera_id}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    content_type = image.get("content_type") or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=300"}
    if image.get("path"):
        return FileResponse(image["path"], media_type=content_type, headers=headers)
    data = image.get("bytes") or b""
    if not data:
        raise HTTPException(status_code=404, detail="frame image not found")
    return Response(content=data, media_type=content_type, headers=headers)


@app.post("/cameras/{camera_id}/snapshot", response_model=list[Detection])
async def force_snapshot(camera_id: str, request: Request, user: dict = Depends(current_operator)) -> list[Detection]:
    camera = next((item for item in CAMERAS if item["id"] == camera_id), None)
    if camera is None or not camera.get("active", True):
        return []
    payload = await process_camera(camera, include_image=False)
    await audit(user, "camera.snapshot", camera_id, {"detections": len(payload.get("detections", []))}, request)
    return [Detection.model_validate(item) for item in payload.get("detections", [])]


@app.get("/processed-frames/latest")
async def latest_processed_frames(user: dict = Depends(current_user)) -> list[dict]:
    return [strip_frame_image(item) for item in latest_frames.values()]


@app.get("/cameras/{camera_id}/frames")
async def camera_frames(
    camera_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    before_id: int | None = Query(None, ge=1),
    start: str | None = None,
    end: str | None = None,
    include_empty: bool = True,
    user: dict = Depends(current_user),
) -> list[dict]:
    camera = get_camera_from_cache(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", camera_id, {"camera_id": camera_id, "scope": "timeline"}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")
    start_iso = normalize_event_timestamp(start) if start else None
    end_iso = normalize_event_timestamp(end) if end else None
    if start_iso and end_iso and start_iso > end_iso:
        raise HTTPException(status_code=400, detail="start must be before end")
    details = {"limit": limit, "before_id": before_id, "start": start_iso, "end": end_iso, "include_empty": include_empty}
    await audit(user, "frame.timeline", camera_id, details, request)
    return await asyncio.to_thread(
        list_frames_for_camera,
        camera_id,
        min(max(limit, 1), 500),
        before_id,
        start_iso,
        end_iso,
        include_empty,
    )


@app.get("/cameras/{camera_id}/processed-frame")
async def processed_frame(camera_id: str, request: Request, refresh: bool = False, user: dict = Depends(current_user)) -> dict:
    camera = next((item for item in CAMERAS if item["id"] == camera_id), None)
    if camera is None:
        return {"camera": None, "detections": []}
    return await load_camera_processed_frame(camera, request, user, refresh=refresh)


async def load_camera_processed_frame(camera: dict, request: Request, user: dict, refresh: bool = False) -> dict:
    if not can_user_view_camera_frame(user, camera):
        await audit(user, "frame.access_denied", camera["id"], {"camera_id": camera["id"]}, request)
        raise HTTPException(status_code=403, detail="voce nao possui acesso a essa transmissao")

    if not camera.get("active", True):
        return public_frame_payload(latest_frames.get(camera["id"]) or paused_camera_payload(camera))

    if not refresh:
        latest = latest_frames.get(camera["id"])
        frame_id = latest.get("frame_id") if latest else None
        if frame_id:
            stored = await asyncio.to_thread(get_frame, int(frame_id))
            if stored:
                return public_frame_payload(stored)
        stored = await asyncio.to_thread(get_latest_frame_for_camera, camera["id"])
        if stored:
            return public_frame_payload(stored)
        raise HTTPException(status_code=404, detail="processed frame not found for camera")
    return public_frame_payload(await process_camera(camera, include_image=True))


def indoor_processing_camera(floor_camera: dict) -> dict:
    source_url = floor_camera.get("source_url") or floor_camera.get("url") or ""
    return {
        "id": f"INDOOR_{floor_camera['id']}",
        "name": floor_camera.get("name") or f"Câmera interna {floor_camera['id']}",
        "url": source_url,
        "source_url": source_url,
        "original_stream_url": source_url,
        "lat": 0.0,
        "lon": 0.0,
        "azimuth_deg": float(floor_camera.get("direction_deg") or 0),
        "elevation_deg": -8.0,
        "height_m": float(floor_camera.get("mount_height_m") or 2.8),
        "focal_length_px": 900.0,
        "fov_h_deg": float(floor_camera.get("fov_deg") or 80),
        "frame_width": 1280,
        "frame_height": 720,
        "capture_interval_s": 15,
        "active": bool(floor_camera.get("enabled", True)),
        "classes": [0, 1, 2, 3, 5, 7],
        "confidence_min": 0.45,
        "detection_profile": "balanced",
        "yolo_model": "",
        "yolo_imgsz": None,
        "person_confidence_min": None,
        "yolo_iou": None,
        "frame_access": "all",
        "frame_access_users": [],
        "priority_level": 3,
        "event_priority_boost": 25,
        "event_boost_ttl_s": 45,
    }


@app.post("/cameras/{camera_id}/focus")
async def camera_focus(camera_id: str, payload: dict, user: dict = Depends(current_user)) -> dict:
    camera = next((item for item in CAMERAS if item["id"] == camera_id), None)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    focused = bool(payload.get("focused", True))
    priority_scheduler.set_focus(camera_id, focused)
    return {"camera_id": camera_id, "focused": focused}


@app.post("/processing/scan")
async def processing_scan(request: Request, user: dict = Depends(current_operator)) -> dict:
    await refresh_camera_cache()
    now = time.monotonic()
    active_cameras = [camera for camera in CAMERAS if camera.get("active", True)]
    due_at_by_camera = {camera["id"]: camera_next_due.get(camera["id"], 0.0) for camera in active_cameras}
    selected = await select_priority_cameras(active_cameras, due_at_by_camera, now)
    if selected:
        for camera in selected:
            camera_next_due[camera["id"]] = now + float(camera.get("capture_interval_s") or 15)
        if YOLO_BATCH_ENABLED:
            chunks = [selected[index:index + YOLO_BATCH_SIZE] for index in range(0, len(selected), YOLO_BATCH_SIZE)]
            results = await asyncio.gather(
                *(process_camera_batch(chunk, include_image=STORE_FRAME_IMAGES) for chunk in chunks),
                return_exceptions=True,
            )
            log_processing_exceptions(results, "manual-scan-batch")
            payloads = [
                payload
                for result in results
                if not isinstance(result, Exception)
                for payload in result
            ]
        else:
            results = await asyncio.gather(
                *(process_camera(camera, include_image=STORE_FRAME_IMAGES) for camera in selected),
                return_exceptions=True,
            )
            log_processing_exceptions(results, "manual-scan")
            payloads = [payload for payload in results if not isinstance(payload, Exception)]
    else:
        payloads = []
    await audit(
        user,
        "processing.scan",
        "processing",
        {"requested_cameras": len(active_cameras), "processed_cameras": len(payloads)},
        request,
    )
    return {
        "processed": len(payloads),
        "deferred": max(0, len(active_cameras) - len(selected)),
        "active_cameras": len(active_cameras),
        "selected_cameras": [camera["id"] for camera in selected],
        "payloads": [strip_frame_image(payload) for payload in payloads],
    }


@app.websocket("/ws/detections")
async def ws_detections(websocket: WebSocket) -> None:
    user = await websocket_user(websocket)
    if user is None:
        return
    await websocket.accept()
    clients.add(websocket)
    try:
        for item in latest_frames.values():
            await websocket.send_json(build_ws_payload(item))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)


def log_processing_exceptions(results: list, context: str) -> None:
    for result in results:
        if isinstance(result, Exception):
            logger.exception("camera processing failed in %s", context, exc_info=result)


async def processing_loop() -> None:
    while True:
        if not CAMERAS:
            await refresh_camera_cache()
        await capture_manager.reconcile(list(CAMERAS))
        due_cameras = []
        due_at_by_camera = {}
        now = time.monotonic()
        for camera in list(CAMERAS):
            due_at = camera_next_due.get(camera["id"], 0.0)
            if camera.get("active", True) and due_at <= now:
                due_at_by_camera[camera["id"]] = due_at
                due_cameras.append(camera)
        if due_cameras:
            due_cameras = await select_priority_cameras(due_cameras, due_at_by_camera, now)
            for camera in due_cameras:
                camera_next_due[camera["id"]] = now + float(camera.get("capture_interval_s") or 15)
        if due_cameras and YOLO_BATCH_ENABLED:
            tasks = [
                asyncio.create_task(process_camera_batch(due_cameras[index:index + YOLO_BATCH_SIZE], include_image=STORE_FRAME_IMAGES))
                for index in range(0, len(due_cameras), YOLO_BATCH_SIZE)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            log_processing_exceptions(results, "processing-loop-batch")
        elif due_cameras:
            tasks = []
            for camera in due_cameras:
                tasks.append(asyncio.create_task(process_camera(camera, include_image=STORE_FRAME_IMAGES)))
                await asyncio.sleep(PROCESS_CAMERA_DELAY_S)
            results = await asyncio.gather(*tasks, return_exceptions=True)
            log_processing_exceptions(results, "processing-loop")
        await asyncio.sleep(PROCESS_CYCLE_PAUSE_S)


async def select_priority_cameras(due_cameras: list[dict], due_at_by_camera: dict[str, float], now: float) -> list[dict]:
    gate_infos = {}
    for camera in due_cameras:
        max_stale = max(CAPTURE_WORKER_MAX_STALE_S, float(camera.get("capture_interval_s") or 15) * 2)
        captured = capture_manager.get_latest(camera["id"], max_stale)
        _, gate_info = frame_change_gate.should_process(camera, captured)
        gate_infos[camera["id"]] = gate_info
    geofences, rules = await get_active_geofence_config()
    geofence_priority_by_camera = calculate_geofence_priority_by_camera(geofences)
    ranked = priority_scheduler.rank(
        due_cameras,
        now=now,
        latest_frames=latest_frames,
        camera_failures=camera_failures,
        capture_status=capture_manager.snapshot_status(),
        gate_infos=gate_infos,
        due_at_by_camera=due_at_by_camera,
        has_active_geofence_rules=bool(rules),
        geofence_priority_by_camera=geofence_priority_by_camera,
    )
    selected, deferred = priority_scheduler.select(ranked)
    for camera in deferred:
        camera_next_due[camera["id"]] = now + min(2.0, max(0.5, PROCESS_CYCLE_PAUSE_S * 2))
    return selected


async def process_camera(camera: dict, include_image: bool = False) -> dict:
    started = time.perf_counter()
    async with processing_semaphore:
        recent = [item for item in history if item.camera_id == camera["id"]][-6:]
        max_stale = max(CAPTURE_WORKER_MAX_STALE_S, float(camera.get("capture_interval_s") or 15) * 2)
        captured = capture_manager.get_latest(camera["id"], max_stale)
        should_process, gate_info = frame_change_gate.should_process(camera, captured)
        if not should_process and camera["id"] in latest_frames:
            payload = build_unchanged_frame_payload(camera, captured, latest_frames[camera["id"]], gate_info, started)
            latest_frames[camera["id"]] = strip_frame_image(payload)
            await broadcast(build_ws_payload(payload))
            schedule_next_camera_run(camera, payload)
            await asyncio.to_thread(write_processing_benchmark_log, payload, 0.0, payload["benchmark"]["total_ms"])
            return payload
        payload = await asyncio.to_thread(build_processed_frame, camera, recent, include_image, captured)
        payload.setdefault("benchmark", {})["frame_change_gate"] = gate_info
        if is_payload_healthy(payload):
            frame_change_gate.mark_processed(camera["id"], captured)
    save_started = time.perf_counter()
    frame_id = await asyncio.to_thread(save_processed_frame, payload)
    save_ms = (time.perf_counter() - save_started) * 1000
    payload["frame_id"] = frame_id
    await apply_temporal_analysis(payload, frame_id)
    latest_frames[camera["id"]] = strip_frame_image(payload)
    await publish_processed_frame(payload)
    schedule_next_camera_run(camera, payload)
    total_ms = (time.perf_counter() - started) * 1000
    await asyncio.to_thread(write_processing_benchmark_log, payload, save_ms, total_ms)
    return payload


async def process_camera_batch(cameras: list[dict], include_image: bool = False) -> list[dict]:
    if len(cameras) == 1:
        return [await process_camera(cameras[0], include_image)]
    started = time.perf_counter()
    skipped_payloads: list[dict] = []
    async with processing_semaphore:
        histories = {}
        captured_frames = {}
        process_cameras = []
        gate_infos = {}
        for camera in cameras:
            max_stale = max(CAPTURE_WORKER_MAX_STALE_S, float(camera.get("capture_interval_s") or 15) * 2)
            captured = capture_manager.get_latest(camera["id"], max_stale)
            should_process, gate_info = frame_change_gate.should_process(camera, captured)
            gate_infos[camera["id"]] = gate_info
            if not should_process and camera["id"] in latest_frames:
                skipped_payloads.append(build_unchanged_frame_payload(camera, captured, latest_frames[camera["id"]], gate_info, started))
                continue
            process_cameras.append(camera)
            captured_frames[camera["id"]] = captured
            histories[camera["id"]] = [item for item in history if item.camera_id == camera["id"]][-6:]
        payloads = await asyncio.to_thread(build_processed_frames_batch, process_cameras, histories, include_image, captured_frames)

    for payload in payloads:
        camera_id = payload["camera"]["id"]
        payload.setdefault("benchmark", {})["frame_change_gate"] = gate_infos.get(camera_id, {})
        if is_payload_healthy(payload):
            frame_change_gate.mark_processed(camera_id, captured_frames.get(camera_id))
        save_started = time.perf_counter()
        frame_id = await asyncio.to_thread(save_processed_frame, payload)
        save_ms = (time.perf_counter() - save_started) * 1000
        payload["frame_id"] = frame_id
        await apply_temporal_analysis(payload, frame_id)
        latest_frames[camera_id] = strip_frame_image(payload)
        await publish_processed_frame(payload)
        schedule_next_camera_run(payload["camera"], payload)
        total_ms = (time.perf_counter() - started) * 1000
        await asyncio.to_thread(write_processing_benchmark_log, payload, save_ms, total_ms)
    for payload in skipped_payloads:
        camera_id = payload["camera"]["id"]
        latest_frames[camera_id] = strip_frame_image(payload)
        await broadcast(build_ws_payload(payload))
        schedule_next_camera_run(payload["camera"], payload)
        await asyncio.to_thread(write_processing_benchmark_log, payload, 0.0, payload["benchmark"]["total_ms"])
    return payloads + skipped_payloads


def build_unchanged_frame_payload(camera: dict, captured: dict | None, previous_payload: dict, gate_info: dict, started: float) -> dict:
    previous_frame = previous_payload.get("frame") or {}
    detections = deepcopy(previous_payload.get("detections") or [])
    timestamp = datetime.now(timezone.utc).isoformat()
    for detection in detections:
        detection["timestamp"] = timestamp
    benchmark = {
        "resolve_ms": float((captured or {}).get("resolve_ms") or 0),
        "capture_ms": float((captured or {}).get("capture_ms") or 0),
        "capture_age_ms": round((time.monotonic() - float((captured or {}).get("captured_monotonic", time.monotonic()))) * 1000, 3),
        "capture_source": "memory-worker",
        "capture_worker_mode": (captured or {}).get("mode", "unknown"),
        "inference_ms": 0.0,
        "batch_size": 0,
        "batch_item_ms": 0.0,
        "frame_change_gate": gate_info,
    }
    benchmark["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return {
        "camera": camera,
        "timestamp": timestamp,
        "source": (captured or {}).get("source") or previous_payload.get("source") or {},
        "frame": {
            "width": previous_frame.get("width") or camera.get("frame_width", 1280),
            "height": previous_frame.get("height") or camera.get("frame_height", 720),
            "mode": "frame-unchanged",
            "image": "",
        },
        "detections": detections,
        "benchmark": benchmark,
        "frame_id": previous_payload.get("frame_id"),
    }


def schedule_next_camera_run(camera: dict, payload: dict) -> None:
    camera_id = camera["id"]
    interval = float(camera.get("capture_interval_s") or 15)
    if is_payload_healthy(payload):
        camera_failures[camera_id] = 0
        degraded_cameras.pop(camera_id, None)
        camera_next_due[camera_id] = time.monotonic() + interval
        return
    failures = camera_failures.get(camera_id, 0) + 1
    camera_failures[camera_id] = failures
    frame = payload.get("frame") or {}
    error = payload.get("error") or frame.get("mode") or "capture-error"
    if CAMERA_AUTO_DEGRADE_ENABLED and failures >= CAMERA_DEGRADE_AFTER_FAILURES:
        degraded_cameras[camera_id] = {
            "since": datetime.now(timezone.utc).isoformat(),
            "failures": failures,
            "reason": str(error),
            "retry_in_s": CAMERA_DEGRADE_BACKOFF_S,
        }
        camera_next_due[camera_id] = time.monotonic() + CAMERA_DEGRADE_BACKOFF_S
        return
    backoff = min(OFFLINE_BACKOFF_MAX_S, max(OFFLINE_BACKOFF_BASE_S, interval) * (2 ** min(failures - 1, 4)))
    camera_next_due[camera_id] = time.monotonic() + backoff


async def retention_loop() -> None:
    while True:
        await asyncio.sleep(RETENTION_INTERVAL_S)
        await asyncio.to_thread(run_retention_once)


def run_retention_once() -> dict:
    old_frames = purge_old_frames(RETENTION_MAX_FRAME_AGE_DAYS)
    orphan_files = purge_orphan_frame_files()
    return {
        "old_frames": old_frames,
        "orphan_files": orphan_files,
        "max_age_days": RETENTION_MAX_FRAME_AGE_DAYS,
    }


def resolve_benchmark_source(source: str) -> Path:
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


async def run_stress_benchmark_job(job_id: str, payload: StressBenchmarkRequest, source_path: Path) -> None:
    job = benchmark_jobs[job_id]
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    results_dir = BENCHMARKS_DIR / "results"
    reports_dir = BENCHMARKS_DIR / "reports"
    results_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        for cameras_count in payload.cameras:
            scenario_slug = f"stress_{job_id}_{cameras_count}cams"
            out_path = results_dir / f"{scenario_slug}.jsonl"
            summary_path = results_dir / f"{scenario_slug}_summary.json"
            report_path = reports_dir / f"{scenario_slug}_report.html"
            scenario = {
                "cameras": cameras_count,
                "status": "running",
                "jsonl": artifact_url(out_path),
                "summary": artifact_url(summary_path),
                "report": artifact_url(report_path),
                "summary_data": None,
                "error": None,
            }
            job["scenarios"].append(scenario)
            cmd = [
                sys.executable,
                str(BENCHMARKS_DIR / "stress_argos_client.py"),
                "--source",
                str(source_path),
                "--cameras",
                str(cameras_count),
                "--interval",
                str(payload.interval),
                "--duration-minutes",
                str(payload.duration_minutes),
                "--workers",
                str(payload.workers),
                "--model",
                payload.model,
                "--device",
                payload.device,
                "--imgsz",
                str(payload.imgsz),
                "--conf",
                str(payload.conf),
                "--out",
                str(out_path),
                "--summary",
                str(summary_path),
                "--report",
                str(report_path),
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            job["log"] += f"\n=== {cameras_count} cameras ===\n{stdout_text}{stderr_text}"
            if process.returncode != 0:
                scenario["status"] = "error"
                scenario["error"] = stderr_text or stdout_text or f"return code {process.returncode}"
                raise RuntimeError(scenario["error"])
            scenario["status"] = "done"
            if summary_path.exists():
                scenario["summary_data"] = json.loads(summary_path.read_text(encoding="utf-8"))
        job["comparison"] = write_stress_comparison(job_id, job["scenarios"], payload)
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        job["ended_at"] = datetime.now(timezone.utc).isoformat()


def write_stress_comparison(job_id: str, scenarios: list[dict], payload: StressBenchmarkRequest) -> dict:
    rows = []
    for scenario in scenarios:
        summary = scenario.get("summary_data") or {}
        processing = summary.get("processing_summary", {})
        latency = summary.get("latency_summary", {})
        queue = summary.get("queue_summary", {})
        resources = summary.get("resource_summary", {})
        rows.append(
            {
                "cameras": scenario.get("cameras"),
                "frames": f"{processing.get('frames_processed', 0)}/{processing.get('frames_expected', 0)}",
                "errors": processing.get("frames_error", 0),
                "demand_fps": processing.get("demand_fps", 0),
                "measured_fps": processing.get("measured_fps", 0),
                "avg_latency_ms": latency.get("avg_latency_ms", 0),
                "p95_latency_ms": latency.get("p95_latency_ms", 0),
                "p99_latency_ms": latency.get("p99_latency_ms", 0),
                "max_queue_size": queue.get("max_queue_size", 0),
                "avg_cpu_percent": resources.get("avg_cpu_percent", 0),
                "max_cpu_percent": resources.get("max_cpu_percent", 0),
                "status": processing.get("viability_status", "indefinido"),
            }
        )
    json_path = BENCHMARKS_DIR / "results" / f"stress_{job_id}_comparison.json"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    png_path = BENCHMARKS_DIR / "reports" / f"stress_{job_id}_comparison.png"
    try:
        write_stress_comparison_png(rows, png_path, payload)
        png_url = artifact_url(png_path)
    except Exception:
        png_url = ""
    html_path = BENCHMARKS_DIR / "reports" / f"stress_{job_id}_comparison.html"
    html_path.write_text(render_stress_comparison_html(rows, payload, png_url), encoding="utf-8")
    return {
        "json": artifact_url(json_path),
        "html": artifact_url(html_path),
        "png": png_url,
    }


def write_stress_comparison_png(rows: list[dict], output_path: Path, payload: StressBenchmarkRequest) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cameras = [int(row["cameras"]) for row in rows]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle(
        f"Kaelis Argos - Stress simulado ({payload.interval}s, {payload.workers} workers, imgsz {payload.imgsz})",
        fontsize=14,
        fontweight="bold",
    )
    axes[0, 0].plot(cameras, [float(row["demand_fps"]) for row in rows], marker="o", label="FPS demandado")
    axes[0, 0].plot(cameras, [float(row["measured_fps"]) for row in rows], marker="o", label="FPS medido")
    axes[0, 0].set_title("Demanda vs throughput")
    axes[0, 0].set_xlabel("cameras")
    axes[0, 0].set_ylabel("FPS")
    axes[0, 0].grid(True, alpha=.25)
    axes[0, 0].legend()

    axes[0, 1].plot(cameras, [float(row["avg_latency_ms"]) for row in rows], marker="o", label="media")
    axes[0, 1].plot(cameras, [float(row["p95_latency_ms"]) for row in rows], marker="o", label="P95")
    axes[0, 1].plot(cameras, [float(row["p99_latency_ms"]) for row in rows], marker="o", label="P99")
    axes[0, 1].set_title("Latencia total")
    axes[0, 1].set_xlabel("cameras")
    axes[0, 1].set_ylabel("ms")
    axes[0, 1].grid(True, alpha=.25)
    axes[0, 1].legend()

    axes[1, 0].bar([str(item) for item in cameras], [float(row["max_queue_size"]) for row in rows], color="#2457a6")
    axes[1, 0].set_title("Pico de fila")
    axes[1, 0].set_xlabel("cameras")
    axes[1, 0].set_ylabel("frames")
    axes[1, 0].grid(axis="y", alpha=.25)

    axes[1, 1].plot(cameras, [float(row["avg_cpu_percent"]) for row in rows], marker="o", label="CPU media")
    axes[1, 1].plot(cameras, [float(row["max_cpu_percent"]) for row in rows], marker="o", label="CPU max")
    axes[1, 1].set_title("CPU")
    axes[1, 1].set_xlabel("cameras")
    axes[1, 1].set_ylabel("%")
    axes[1, 1].set_ylim(0, 105)
    axes[1, 1].grid(True, alpha=.25)
    axes[1, 1].legend()
    fig.tight_layout(rect=[0, 0, 1, .96])
    fig.savefig(output_path, dpi=140)
    plt.close(fig)


def render_stress_comparison_html(rows: list[dict], payload: StressBenchmarkRequest, png_url: str) -> str:
    headers = list(rows[0].keys()) if rows else []
    table = "<table><thead><tr>" + "".join(f"<th>{html.escape(str(header))}</th>" for header in headers) + "</tr></thead><tbody>"
    for row in rows:
        table += "<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>"
    table += "</tbody></table>"
    image = f'<img src="{html.escape(png_url)}" alt="Comparativo de stress">' if png_url else "<p>Grafico PNG indisponivel.</p>"
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Stress comparison</title>
<style>body{{font-family:Inter,Arial,sans-serif;background:#0b0d10;color:#e8edf2;margin:0;padding:32px}}main{{max-width:1180px;margin:auto}}img{{max-width:100%;background:#fff;border-radius:6px}}table{{width:100%;border-collapse:collapse;margin-top:24px;font-size:13px}}th,td{{border-bottom:1px solid #2b3139;padding:8px;text-align:left}}th{{background:#151a20}}a{{color:#9cc7ff}}</style></head><body><main>
<h1>Kaelis Argos - Stress simulado</h1>
<p>Intervalo {payload.interval}s, duracao {payload.duration_minutes} min, workers {payload.workers}, device {html.escape(payload.device)}, imgsz {payload.imgsz}.</p>
{image}
{table}
</main></body></html>"""


def artifact_url(path: Path) -> str:
    relative = path.resolve().relative_to(BENCHMARKS_DIR.resolve())
    return f"/benchmark-artifacts/{relative.as_posix()}"


def public_benchmark_job(job: dict) -> dict:
    output = deepcopy(job)
    log = output.get("log") or ""
    output["log_tail"] = log[-6000:]
    output.pop("log", None)
    return output


def is_payload_healthy(payload: dict) -> bool:
    frame = payload.get("frame") or {}
    return frame.get("mode") in {"yolo", "yolo-batch", "frame-unchanged"}


async def publish_processed_frame(payload: dict) -> None:
    detections = [Detection.model_validate(item) for item in payload.get("detections", [])]
    for detection in detections:
        history.append(detection)
    alerts = await evaluate_geofence_alerts(payload)
    temporal_events = payload.get("temporal_events") or []
    camera = payload.get("camera") or {}
    if camera.get("id"):
        priority_scheduler.record_observation(
            camera["id"],
            len(detections),
            boost=float(camera.get("event_priority_boost") or 25),
            ttl_s=float(camera.get("event_boost_ttl_s") or 45),
            reason="temporal-alert" if temporal_events else "geofence-alert" if alerts else "event",
        )
    if alerts and is_feature_enabled("telegram_enabled"):
        telegram_results = await asyncio.gather(
            *(asyncio.to_thread(send_telegram_alert, alert, payload) for alert in alerts)
        )
        for alert, result in zip(alerts, telegram_results):
            alert["telegram"] = result
    ws_payload = build_ws_payload(payload)
    if alerts:
        ws_payload["geofence_alerts"] = alerts
    if temporal_events:
        ws_payload["temporal_events"] = temporal_events
    await broadcast(ws_payload)
    for alert in alerts:
        await broadcast({"event": "geofence_alert", "alert": alert})
    for event in temporal_events:
        await broadcast({"event": "temporal_event", "temporal_event": event})


async def apply_temporal_analysis(payload: dict, frame_id: int | None = None) -> dict:
    if not is_payload_healthy(payload):
        return payload
    geofences, rules = await get_active_geofence_config()
    temporal_tracks = temporal_tracker.process_frame(payload, geofences, frame_id)
    payload["tracks"] = temporal_tracks
    for track in temporal_tracks:
        await asyncio.to_thread(upsert_object_track, track)
        await asyncio.to_thread(save_track_history, track)
    active_track_objects = [
        track
        for track in temporal_tracker.tracks.values()
        if track.status not in {"closed", "stale"}
    ]
    temporal_events = evaluate_temporal_geofence_rules(active_track_objects, rules, config=temporal_tracker.config)
    saved_events = []
    for event in temporal_events:
        saved_events.append(await asyncio.to_thread(save_temporal_event, event))
    if saved_events:
        payload["temporal_events"] = saved_events
    return payload

async def broadcast(payload: dict) -> None:
    stale_clients: list[WebSocket] = []
    for websocket in clients:
        try:
            await websocket.send_json(payload)
        except RuntimeError:
            stale_clients.append(websocket)
    for websocket in stale_clients:
        clients.discard(websocket)


def build_ws_payload(payload: dict) -> dict:
    return {
        "event": "processed_frame",
        **strip_frame_image(payload),
    }


def public_frame_payload(payload: dict) -> dict:
    item = deepcopy(payload)
    if item.get("camera"):
        item["camera"] = public_camera(item["camera"])
    if isinstance(item.get("source"), dict):
        for key in ("player_url", "hls_url", "poster_url"):
            if item["source"].get(key):
                item["source"][key] = scrub_url(str(item["source"][key]))
    return item


def strip_frame_image(payload: dict) -> dict:
    item = public_frame_payload(payload)
    if item.get("frame"):
        item["frame"]["image"] = ""
    return item


def extract_chat_mentions(body: str) -> dict[str, list]:
    users = sorted({item for item in re.findall(r"@([A-Za-z0-9_.-]{3,64})", body or "")})
    geofences = sorted({int(item) for item in re.findall(r"#geofence:(\d+)", body or "")})
    events = sorted({int(item) for item in re.findall(r"#evento:(\d+)|#event:(\d+)", body or "") for item in item if item})
    frames = sorted({int(item) for item in re.findall(r"#frame:(\d+)", body or "")})
    cameras = sorted({item for item in re.findall(r"#camera:([A-Za-z0-9_.:-]+)", body or "")})
    return {
        "users": users,
        "geofences": geofences,
        "events": events,
        "frames": frames,
        "cameras": cameras,
    }


def paused_camera_payload(camera: dict) -> dict:
    return {
        "camera": camera,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "player_url": camera.get("original_stream_url") or camera.get("url", ""),
            "hls_url": "",
            "poster_url": "",
            "error": "camera paused",
        },
        "frame": {
            "width": camera.get("frame_width", 1280),
            "height": camera.get("frame_height", 720),
            "mode": "camera-paused",
            "image": "",
        },
        "detections": [],
    }


async def evaluate_geofence_alerts(payload: dict) -> list[dict]:
    # Temporal geofence rules are evaluated in apply_temporal_analysis().
    # Keep the legacy endpoint/table readable, but do not create single-frame
    # alerts here; Argos events require prior temporal evidence.
    return []
    detections = payload.get("detections") or []
    if not detections:
        return []
    geofences, rules = await get_active_geofence_config()
    if not geofences or not rules:
        return []
    geofences_by_id = {item["id"]: item for item in geofences}
    grouped_matches: dict[str, dict] = {}
    for detection in detections:
        geo = detection.get("geo") or {}
        lat = geo.get("lat")
        lon = geo.get("lon")
        if lat is None or lon is None:
            continue
        for rule in rules:
            geofence = geofences_by_id.get(rule["geofence_id"])
            if not geofence:
                continue
            if rule["object_type"] != "todos" and rule["object_type"] != detection.get("object_type"):
                continue
            if not is_detection_in_daily_window(detection.get("timestamp", payload.get("timestamp", "")), rule["start_time"], rule["end_time"]):
                continue
            if not point_in_polygon(float(lat), float(lon), geofence["points"]):
                continue
            group_key = "|".join(
                [
                    str(rule["id"]),
                    str(geofence["id"]),
                    str(payload.get("frame_id") or payload.get("timestamp", "")),
                    detection.get("object_type", ""),
                ]
            )
            grouped = grouped_matches.setdefault(
                group_key,
                {
                    "event": "geofence_alert",
                    "timestamp": detection.get("timestamp", payload.get("timestamp", "")),
                    "geofence_id": geofence["id"],
                    "geofence_name": geofence["name"],
                    "geofence_priority": geofence.get("priority_level", 3),
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "object_type": detection.get("object_type"),
                    "detection": detection,
                    "detections": [],
                    "count": 0,
                },
            )
            grouped["detections"].append(detection)
            grouped["count"] += 1
    alerts = []
    for alert_key, alert in grouped_matches.items():
        if alert_key in recent_geofence_alert_keys:
            continue
        recent_geofence_alert_keys.append(alert_key)
        alert["id"] = await asyncio.to_thread(save_geofence_alert, alert)
        detection = alert.get("detection") or {}
        incident = await asyncio.to_thread(
            create_incident,
            {
                "title": f"Alerta de geofence: {alert.get('rule_name') or alert.get('geofence_name') or alert['id']}",
                "summary": alert.get("message") or f"{alert.get('object_type') or detection.get('object_type') or 'objeto'} em {alert.get('geofence_name') or 'geofence'}",
                "status": "open",
                "severity": alert.get("severity") or "high",
                "source_kind": "geofence_alert",
                "source_key": f"geofence_alert:{alert['id']}",
                "camera_id": detection.get("camera_id") or alert.get("camera_id") or "",
                "event_id": None,
                "frame_id": detection.get("frame_id") or alert.get("frame_id"),
                "track_id": detection.get("track_id") or "",
                "object_type": detection.get("object_type") or alert.get("object_type") or "",
                "assigned_to": "",
                "opened_by": "sistema",
                "metadata": {
                    "alert": alert,
                    "geo": detection.get("geo") or {},
                    "detection": detection,
                },
                "timeline_message": "Ocorrencia aberta automaticamente por alerta de geofence.",
                "timeline_payload": {"geofence_alert_id": alert["id"], "alert_key": alert_key},
            },
            "sistema",
        )
        alert["incident_id"] = incident.get("id")
        await asyncio.to_thread(update_geofence_alert, int(alert["id"]), {"incident_id": incident.get("id")}, "sistema")
        alerts.append(alert)
    return alerts


def calculate_geofence_priority_by_camera(geofences: list[dict]) -> dict[str, int]:
    if not geofences:
        return {}
    priority_by_camera: dict[str, int] = {}
    for camera_id, latest in latest_frames.items():
        for detection in latest.get("detections") or []:
            geo = detection.get("geo") or {}
            lat = geo.get("lat")
            lon = geo.get("lon")
            if lat is None or lon is None:
                continue
            for geofence in geofences:
                if not point_in_polygon(float(lat), float(lon), geofence.get("points") or []):
                    continue
                priority_by_camera[camera_id] = max(
                    priority_by_camera.get(camera_id, 0),
                    int(geofence.get("priority_level") or 3),
                )
    return priority_by_camera


async def get_active_geofence_config() -> tuple[list[dict], list[dict]]:
    now = time.monotonic()
    if now < float(active_geofence_cache.get("expires_at") or 0):
        return list(active_geofence_cache["geofences"]), list(active_geofence_cache["rules"])
    geofences, rules = await asyncio.gather(
        asyncio.to_thread(list_geofences, True),
        asyncio.to_thread(list_geofence_rules, True),
    )
    active_geofence_cache["geofences"] = geofences
    active_geofence_cache["rules"] = rules
    active_geofence_cache["expires_at"] = now + 10
    return geofences, rules


def invalidate_geofence_cache() -> None:
    active_geofence_cache["expires_at"] = 0.0


def point_in_polygon(lat: float, lon: float, points: list[dict]) -> bool:
    inside = False
    if len(points) < 3:
        return False
    j = len(points) - 1
    for i, point in enumerate(points):
        yi = float(point["lat"])
        xi = float(point["lon"])
        yj = float(points[j]["lat"])
        xj = float(points[j]["lon"])
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def is_detection_in_daily_window(timestamp: str, start_time: str, end_time: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = parsed.astimezone(timezone.utc).strftime("%H:%M")
    if start_time <= end_time:
        return start_time <= current <= end_time
    return current >= start_time or current <= end_time


def send_telegram_alert(alert: dict, payload: dict) -> dict:
    caption = format_telegram_alert_caption(alert)
    image = extract_frame_image(payload)
    if image:
        return send_telegram_photo(caption, image[0], image[1])
    result = send_telegram_message(caption)
    if result.get("sent"):
        result["fallback"] = "text_without_frame"
    return result


def send_telegram_message(text: str) -> dict:
    config = get_telegram_config(mask_token=False)
    readiness = telegram_readiness(config)
    if readiness:
        return readiness
    try:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{config['bot_token']}/sendMessage",
            data={
                "chat_id": config["chat_id"],
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return telegram_response_result(response)
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}


def send_telegram_photo(caption: str, image_bytes: bytes, content_type: str = "image/jpeg") -> dict:
    config = get_telegram_config(mask_token=False)
    readiness = telegram_readiness(config)
    if readiness:
        return readiness
    try:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{config['bot_token']}/sendPhoto",
            data={
                "chat_id": config["chat_id"],
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"photo": ("frame-gatilho.jpg", image_bytes, content_type)},
            timeout=15,
        )
        return telegram_response_result(response)
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}


def telegram_readiness(config: dict) -> dict | None:
    if not config.get("enabled"):
        return {"sent": False, "reason": "telegram_disabled"}
    if not config.get("bot_token") or not config.get("chat_id"):
        return {"sent": False, "reason": "telegram_not_configured"}
    return None


def telegram_response_result(response) -> dict:
    if response.ok:
        return {"sent": True, "status_code": response.status_code}
    return {
        "sent": False,
        "status_code": response.status_code,
        "reason": response.text[:240],
    }


def extract_frame_image(payload: dict) -> tuple[bytes, str] | None:
    image = ((payload.get("frame") or {}).get("image") or "").strip()
    if not image:
        return None
    content_type = "image/jpeg"
    if image.startswith("data:"):
        header, _, data = image.partition(",")
        image = data
        media_type = header.removeprefix("data:").split(";")[0]
        if media_type:
            content_type = media_type
    try:
        return base64.b64decode(image), content_type
    except Exception:
        return None


def format_telegram_alert_caption(alert: dict) -> str:
    timestamp = alert.get("timestamp") or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        time_label = parsed.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
    except ValueError:
        time_label = str(timestamp)
    detection = alert.get("detection") or {}
    camera_name = detection.get("camera_name") or detection.get("camera_id") or "camera"
    count = int(alert.get("count") or 1)
    object_type = alert.get("object_type") or detection.get("object_type") or "objeto"
    geofence_name = alert.get("geofence_name") or "geofence"
    rule_name = alert.get("rule_name") or "regra"
    confidence = detection.get("confidence")
    confidence_text = f"{float(confidence) * 100:.0f}%" if confidence is not None else "--"
    return (
        f"<b>{html.escape(time_label)}</b> "
        f"{count} deteccao(es) de <b>{html.escape(str(object_type))}</b> em "
        f"<b>{html.escape(str(geofence_name))}</b>\n"
        f"Regra: {html.escape(str(rule_name))}\n"
        f"Camera: {html.escape(str(camera_name))}\n"
        f"Confianca: {html.escape(confidence_text)}"
    )


def validate_polygon_points(points: list[dict]) -> None:
    if len(points) < 3:
        raise HTTPException(status_code=400, detail="geofence requires at least 3 points")
    for point in points:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None or not -90 <= float(lat) <= 90 or not -180 <= float(lon) <= 180:
            raise HTTPException(status_code=400, detail="invalid geofence point")


def validate_floor_camera_position(plan: dict, x_px: float, y_px: float) -> None:
    width = float(plan.get("image_width_px") or 0)
    height = float(plan.get("image_height_px") or 0)
    if width <= 0 or height <= 0:
        return
    if not 0 <= float(x_px) <= width or not 0 <= float(y_px) <= height:
        raise HTTPException(status_code=400, detail="camera position must be inside the floor plan image")


def validate_floor_polygon(plan: dict, polygon: list[dict]) -> None:
    if len(polygon) < 3:
        raise HTTPException(status_code=400, detail="floor zone requires at least 3 points")
    width = float(plan.get("image_width_px") or 0)
    height = float(plan.get("image_height_px") or 0)
    if width <= 0 or height <= 0:
        return
    for point in polygon:
        x = point.get("x")
        y = point.get("y")
        if x is None or y is None or not 0 <= float(x) <= width or not 0 <= float(y) <= height:
            raise HTTPException(status_code=400, detail="floor zone point must be inside the floor plan")


def validate_daily_time(value: str) -> None:
    if not re.match(r"^\d{2}:\d{2}$", value or ""):
        raise HTTPException(status_code=400, detail="time must be HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23 or minute > 59:
        raise HTTPException(status_code=400, detail="time must be HH:MM")


def slug_camera_id(name: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    base = f"CAM_{slug or 'NEW'}"
    candidate = base
    counter = 2
    existing = {item["id"] for item in CAMERAS}
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def normalize_event_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


async def audit(
    user: dict,
    action: str,
    resource: str,
    details: dict | None = None,
    request: Request | None = None,
) -> None:
    await asyncio.to_thread(
        save_audit_log,
        user.get("username", "anonymous"),
        user.get("role", "anonymous"),
        action,
        resource,
        details or {},
        request_ip(request),
    )


def request_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return ""
    forwarded_for = request.headers.get("x-forwarded-for") if TRUST_PROXY_HEADERS else ""
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host


def login_rate_keys(request: Request, username: str) -> tuple[str, str]:
    ip = request_ip(request) or "unknown"
    normalized_username = str(username or "").strip().lower()[:128] or "unknown"
    return f"login:ip:{ip}", f"login:pair:{ip}:{normalized_username}"


def prune_login_rate_state(now: float) -> None:
    if len(login_rate_attempts) + len(login_rate_lockouts) <= LOGIN_RATE_LIMIT_MAX_KEYS:
        return
    stale_attempts = [
        key
        for key, attempts in login_rate_attempts.items()
        if not attempts or attempts[-1] <= now - LOGIN_RATE_LIMIT_WINDOW_S
    ]
    for key in stale_attempts:
        login_rate_attempts.pop(key, None)
    stale_lockouts = [key for key, until in login_rate_lockouts.items() if until <= now]
    for key in stale_lockouts:
        login_rate_lockouts.pop(key, None)


def check_login_rate_limit(request: Request, username: str) -> None:
    now = time.monotonic()
    keys = login_rate_keys(request, username)
    with login_rate_lock:
        prune_login_rate_state(now)
        retry_after = max((login_rate_lockouts.get(key, 0.0) - now for key in keys), default=0.0)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="too many login attempts",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def record_failed_login_attempt(request: Request, username: str) -> int:
    now = time.monotonic()
    keys = login_rate_keys(request, username)
    limits = {
        keys[0]: LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS,
        keys[1]: LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    }
    lockout_until = 0.0
    with login_rate_lock:
        for key in keys:
            attempts = login_rate_attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - LOGIN_RATE_LIMIT_WINDOW_S:
                attempts.popleft()
            attempts.append(now)
            if len(attempts) >= limits[key]:
                login_rate_lockouts[key] = now + LOGIN_RATE_LIMIT_LOCKOUT_S
                attempts.clear()
                lockout_until = max(lockout_until, login_rate_lockouts[key])
        prune_login_rate_state(now)
    return max(0, int(lockout_until - now))


def clear_login_rate_limit(request: Request, username: str) -> None:
    keys = login_rate_keys(request, username)
    with login_rate_lock:
        for key in keys:
            login_rate_attempts.pop(key, None)
            login_rate_lockouts.pop(key, None)


def resolve_ip_camera_stream(payload: dict, password: str = "") -> tuple[str, dict]:
    gateway = {
        "kind": "ip-camera",
        "host": payload.get("host") or "",
        "rtsp_port": int(payload.get("rtsp_port") or 554),
        "onvif_port": int(payload.get("onvif_port") or 80),
        "username": payload.get("username") or "",
        "rtsp_url_manual": payload.get("rtsp_url_manual") or "",
        "manufacturer": payload.get("manufacturer") or "",
        "model": payload.get("model") or "",
        "selected_profile_token": payload.get("selected_profile_token") or "",
        "selected_stream_role": payload.get("selected_stream_role") or "substream",
        "status": "offline",
    }
    manual_url = str(payload.get("rtsp_url_manual") or "").strip()
    if manual_url:
        try:
            validate_safe_url(manual_url, context="rtsp_url_manual", allowed_schemes={"rtsp", "http", "https"})
        except UnsafeNetworkTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        gateway["source"] = "rtsp-manual"
        gateway["rtsp_url"] = scrub_stream_url(manual_url)
        return manual_url, gateway
    if not payload.get("use_onvif", True):
        raise HTTPException(status_code=400, detail="rtsp_url_manual is required when ONVIF is disabled")
    if not gateway["host"]:
        raise HTTPException(status_code=400, detail="host or rtsp_url_manual is required")
    try:
        validate_safe_host(gateway["host"], context="onvif_host")
    except UnsafeNetworkTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = GatewayCameraConfig(
        name=payload.get("name") or "",
        host=gateway["host"],
        rtsp_port=gateway["rtsp_port"],
        onvif_port=gateway["onvif_port"],
        username=gateway["username"],
        password=password,
        manufacturer=gateway["manufacturer"],
        model=gateway["model"],
        selected_profile_token=gateway["selected_profile_token"],
        selected_stream_role=gateway["selected_stream_role"],
    )
    try:
        profiles = get_onvif_profiles(config)
    except Exception as exc:
        gateway["source"] = "onvif-failed"
        gateway["status"] = "error"
        gateway["error"] = str(exc)
        raise HTTPException(status_code=502, detail=f"ONVIF unavailable and no manual RTSP URL was provided: {exc}") from exc
    selected = None
    if gateway["selected_profile_token"]:
        selected = next((item for item in profiles if item.token == gateway["selected_profile_token"]), None)
    if selected is None:
        selected = select_preferred_profile(profiles)
    if selected is None or not selected.rtsp_uri:
        gateway["source"] = "onvif-no-stream"
        raise HTTPException(status_code=502, detail="ONVIF did not return an RTSP stream URI")
    try:
        validate_safe_url(selected.rtsp_uri, context="onvif_rtsp_uri", allowed_schemes={"rtsp", "http", "https"})
    except UnsafeNetworkTargetError as exc:
        raise HTTPException(status_code=502, detail=f"ONVIF returned unsafe stream URI: {exc}") from exc
    gateway["source"] = "onvif"
    gateway["selected_profile_token"] = selected.token
    gateway["selected_stream_role"] = selected.role
    gateway["profiles"] = [gateway_public_dict(item) | {"rtsp_uri": scrub_stream_url(item.rtsp_uri)} for item in profiles]
    gateway["rtsp_url"] = scrub_stream_url(selected.rtsp_uri)
    return selected.rtsp_uri, gateway


def ip_camera_payload(camera_id: str, data: dict, stream_url: str, gateway: dict, camera_secret: str = "") -> dict:
    return {
        "id": camera_id,
        "name": data["name"],
        "url": stream_url,
        "source_url": stream_url,
        "original_stream_url": stream_url,
        "lat": float(data.get("lat") or 0),
        "lon": float(data.get("lon") or 0),
        "azimuth_deg": float(data.get("azimuth_deg") or 0),
        "elevation_deg": -10.0,
        "height_m": 12.0,
        "focal_length_px": 900.0,
        "fov_h_deg": 80.0,
        "frame_width": 1280,
        "frame_height": 720,
        "capture_interval_s": 15,
        "active": bool(data.get("active", True)),
        "classes": [0, 1, 2, 3, 5, 7],
        "confidence_min": 0.45,
        "detection_profile": "balanced",
        "yolo_model": "",
        "yolo_imgsz": None,
        "person_confidence_min": None,
        "yolo_iou": None,
        "frame_access": "all",
        "frame_access_users": [],
        "priority_level": 3,
        "event_priority_boost": 25,
        "event_boost_ttl_s": 45,
        "camera_gateway": gateway,
        "camera_secret": camera_secret,
    }


def public_camera(camera: dict) -> dict:
    output = scrub_camera_for_audit(camera)
    if "camera_gateway" in output:
        gateway = deepcopy(output.get("camera_gateway") or {})
        gateway.pop("password", None)
        if gateway.get("rtsp_url"):
            gateway["rtsp_url"] = scrub_stream_url(str(gateway["rtsp_url"]))
        output["camera_gateway"] = gateway
    return output


def public_ip_camera(camera: dict) -> dict:
    output = public_camera(camera)
    gateway = dict(output.get("camera_gateway") or {})
    gateway.pop("password", None)
    if gateway.get("rtsp_url"):
        gateway["rtsp_url"] = scrub_stream_url(str(gateway["rtsp_url"]))
    if isinstance(gateway.get("last_health"), dict) and gateway["last_health"].get("url"):
        gateway["last_health"]["url"] = scrub_stream_url(str(gateway["last_health"]["url"]))
    for profile in gateway.get("profiles") or []:
        if isinstance(profile, dict) and profile.get("rtsp_uri"):
            profile["rtsp_uri"] = scrub_stream_url(str(profile["rtsp_uri"]))
    output["camera_gateway"] = gateway
    output["status"] = gateway.get("status") or ("online" if output.get("active", True) else "offline")
    return output


def protect_camera_password(password: str) -> str:
    if not password:
        return ""
    fernet = camera_fernet()
    if not fernet:
        return ""
    return "fernet:" + fernet.encrypt(password.encode("utf-8")).decode("ascii")


def password_for_camera(camera: dict) -> str:
    secret = str(camera.get("camera_secret") or "")
    if not secret.startswith("fernet:"):
        return ""
    fernet = camera_fernet()
    if not fernet:
        return ""
    try:
        return fernet.decrypt(secret.removeprefix("fernet:").encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def camera_fernet():
    secret_key = os.getenv("ARGOS_CAMERA_SECRET_KEY", "").strip()
    if not secret_key:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest())
    return Fernet(key)


def scrub_camera_for_audit(camera: dict) -> dict:
    output = {}
    for key, value in camera.items():
        if key in {"url", "source_url", "original_stream_url"} and value:
            output[key] = scrub_url(str(value))
        elif key == "camera_secret":
            continue
        else:
            output[key] = value
    return output


def scrub_url(value: str) -> str:
    return re.sub(r"://([^:/?#]+):([^@/?#]+)@", r"://\1:***@", value)


def write_processing_benchmark_log(payload: dict, save_ms: float, total_ms: float) -> None:
    if not ARGOS_BENCHMARK_LOGS:
        return
    camera = payload.get("camera") or {}
    frame = payload.get("frame") or {}
    source = payload.get("source") or {}
    benchmark = payload.get("benchmark") or {}
    detections = payload.get("detections") or []
    row = {
        "timestamp": payload.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "camera_id": camera.get("id"),
        "camera_name": camera.get("name"),
        "frame_id": payload.get("frame_id"),
        "source": "real_pipeline",
        "source_type": source.get("source_type") or "unknown",
        "status": "ok" if frame.get("mode") in {"yolo", "yolo-batch", "frame-unchanged"} else "warning",
        "error": source.get("error"),
        "frame_mode": frame.get("mode"),
        "width": frame.get("width"),
        "height": frame.get("height"),
        "model": os.getenv("YOLO_MODEL", "yolov8n.pt"),
        "device": os.getenv("YOLO_DEVICE", "cpu"),
        "imgsz": int(os.getenv("YOLO_IMGSZ", "512")),
        "conf": camera.get("confidence_min"),
        "detections_count": len(detections),
        "classes": [item.get("object_type") for item in detections],
        "resolve_latency_ms": benchmark.get("resolve_ms", 0),
        "capture_latency_ms": benchmark.get("capture_ms", 0),
        "capture_age_ms": benchmark.get("capture_age_ms", 0),
        "capture_source": benchmark.get("capture_source", "inline"),
        "capture_worker_mode": benchmark.get("capture_worker_mode", ""),
        "inference_latency_ms": benchmark.get("inference_ms", 0),
        "yolo_skipped": frame.get("mode") == "frame-unchanged",
        "frame_change_gate": benchmark.get("frame_change_gate", {}),
        "yolo_batch_size": benchmark.get("batch_size", 1),
        "yolo_batch_item_ms": benchmark.get("batch_item_ms", 0),
        "storage_latency_ms": round(save_ms, 3),
        "total_latency_ms": round(total_ms, 3),
        "queue_size_at_enqueue": 0,
        "queue_size_at_process": 0,
        "queue_lag_ms": 0,
        "saved_to_storage": payload.get("frame_id") is not None,
    }
    if psutil:
        row["cpu_percent"] = psutil.cpu_percent(interval=None)
        row["ram_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 3)
    else:
        row["cpu_percent"] = None
        row["ram_mb"] = None
    ARGOS_BENCHMARK_REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    ARGOS_BENCHMARK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with benchmark_log_lock:
        with ARGOS_BENCHMARK_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_system_status() -> dict:
    disk = shutil.disk_usage(Path.cwd())
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    cpu_count = os.cpu_count() or 1
    cpu_percent = min(100.0, round((load_avg[0] / cpu_count) * 100, 1))
    memory = memory_status()
    network = network_status()
    gpu = gpu_status()
    db_file = DB_PATH if DB_PATH.is_absolute() else Path.cwd() / DB_PATH
    db_size = db_file.stat().st_size if db_file.exists() else 0
    latest_timestamps = [
        item.get("timestamp")
        for item in latest_frames.values()
        if item.get("timestamp")
    ]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "uptime_s": round((datetime.now(timezone.utc) - PROCESS_STARTED_AT).total_seconds()),
        },
        "cpu": {
            "count": cpu_count,
            "load_avg": [round(value, 2) for value in load_avg],
            "estimated_percent": cpu_percent,
            "percent": psutil.cpu_percent(interval=0.05) if psutil else None,
        },
        "memory": memory,
        "gpu": gpu,
        "storage": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0,
            "db_bytes": db_size,
            "database": database_backend(),
        },
        "network": network,
        "processing": {
            "cameras": len(CAMERAS),
            "active_cameras": sum(1 for camera in CAMERAS if camera.get("active", True)),
            "degraded_cameras": degraded_cameras,
            "websocket_clients": len(clients),
            "detections_cached": len(history),
            "processed_frames_cached": len(latest_frames),
            "latest_frame_timestamp": max(latest_timestamps) if latest_timestamps else None,
            "process_concurrency": PROCESS_CONCURRENCY,
            "cycle_pause_s": PROCESS_CYCLE_PAUSE_S,
            "yolo_batch_enabled": YOLO_BATCH_ENABLED,
            "yolo_batch_size": YOLO_BATCH_SIZE,
            "frame_change_gate": frame_change_gate.snapshot_status(),
            "capture_workers": capture_manager.snapshot_status(),
            "retention": {
                "enabled": RETENTION_ENABLED,
                "max_frame_age_days": RETENTION_MAX_FRAME_AGE_DAYS,
                "interval_s": RETENTION_INTERVAL_S,
            },
        },
    }


def build_camera_health_summary(hours: int = 24, limit: int = 100) -> dict:
    window_hours = max(1, min(int(hours or 24), 720))
    item_limit = max(1, min(int(limit or 100), 500))
    now = datetime.now(timezone.utc)
    start_iso = (now - timedelta(hours=window_hours)).isoformat()
    end_iso = now.isoformat()
    capture_status = capture_manager.snapshot_status()
    feedback = feedback_stats(start_iso, end_iso)
    recent_events = list_events_between(start_iso, end_iso, limit=5000)
    events_by_camera: dict[str, list[dict]] = {}
    for event in recent_events:
        camera_id = event.get("camera_id") or (event.get("detection") or {}).get("camera_id")
        if camera_id:
            events_by_camera.setdefault(str(camera_id), []).append(event)

    items = [
        build_camera_health_item(
            camera,
            capture_status,
            events_by_camera.get(str(camera.get("id")), []),
            feedback,
            now,
        )
        for camera in CAMERAS
    ]
    items.sort(key=lambda item: (-int(item.get("risk_score") or 0), item.get("camera_name") or item.get("camera_id") or ""))
    summary = {
        "total": len(items),
        "critical": sum(1 for item in items if item["severity"] == "critical"),
        "high": sum(1 for item in items if item["severity"] == "high"),
        "medium": sum(1 for item in items if item["severity"] == "medium"),
        "low": sum(1 for item in items if item["severity"] == "low"),
        "ok": sum(1 for item in items if item["severity"] == "ok"),
        "problematic": sum(1 for item in items if item["severity"] != "ok"),
    }
    return {
        "generated_at": end_iso,
        "window_hours": window_hours,
        "summary": summary,
        "items": items[:item_limit],
    }


def build_camera_health_item(camera: dict, capture_status: dict, events: list[dict], feedback: dict, now: datetime) -> dict:
    camera_id = str(camera.get("id") or "")
    worker_status = (capture_status.get("cameras") or {}).get(camera_id) or {}
    latest_frame = get_latest_frame_for_camera(camera_id)
    latest_timestamp = (
        (latest_frame or {}).get("timestamp")
        or ((latest_frame or {}).get("frame") or {}).get("timestamp")
        or worker_status.get("timestamp")
    )
    last_frame_age_s = seconds_since_iso(latest_timestamp, now)
    capture_age_s = float(worker_status.get("age_s") or 0) if worker_status else None
    capture_ms = float(worker_status.get("capture_ms") or 0)
    resolve_ms = float(worker_status.get("resolve_ms") or 0)
    max_stale_s = float(capture_status.get("max_stale_s") or CAPTURE_WORKER_MAX_STALE_S)
    confidences = [coerce_float((event.get("detection") or {}).get("confidence") or event.get("confidence")) for event in events]
    confidences = [value for value in confidences if value is not None]
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    false_positive_count = int((feedback.get("false_positives_by_camera") or {}).get(camera_id, 0))
    false_negative_count = int((feedback.get("false_negatives_by_camera") or {}).get(camera_id, 0))
    camera_issue_count = int((feedback.get("camera_issues") or {}).get(camera_id, 0))
    reasons: list[dict] = []
    score = 0

    def add_reason(kind: str, label: str, weight: int, detail: str = "") -> None:
        nonlocal score
        score += weight
        reasons.append({"kind": kind, "label": label, "weight": weight, "detail": detail})

    if camera.get("active") is False:
        add_reason("inactive", "Camera pausada", 45, "A camera esta cadastrada, mas o processamento esta desativado.")
    if worker_status and worker_status.get("ok") is False:
        add_reason("capture_error", "Falha de captura", 55, str(worker_status.get("error") or "A ultima captura nao retornou frame valido."))
    if worker_status and capture_age_s is not None and capture_age_s > max_stale_s:
        add_reason("stale_capture", "Captura atrasada", 40, f"Ultima captura ha {format_health_duration(capture_age_s)}.")
    if not latest_frame:
        add_reason("no_frame", "Sem frame persistido", 28, "Nao ha frame recente salvo para auditoria ou visualizacao.")
    elif last_frame_age_s is not None and last_frame_age_s > 3600:
        add_reason("old_frame", "Frame antigo", 26, f"Ultimo frame salvo ha {format_health_duration(last_frame_age_s)}.")
    if capture_ms > 2500:
        add_reason("high_capture_latency", "Latencia alta de captura", 18, f"{round(capture_ms)} ms para obter frame.")
    if resolve_ms > 3000:
        add_reason("high_resolve_latency", "Latencia alta de origem", 12, f"{round(resolve_ms)} ms para resolver origem.")
    if avg_confidence is not None and len(events) >= 5 and avg_confidence < 0.38:
        add_reason("low_confidence", "Baixa confianca media", 18, f"Media de {round(avg_confidence * 100)}% no periodo.")
    if false_positive_count:
        add_reason("false_positives", "Muitos falsos positivos", min(30, false_positive_count * 8), f"{false_positive_count} feedback(s) no periodo.")
    if false_negative_count:
        add_reason("false_negatives", "Muitos falsos negativos", min(32, false_negative_count * 10), f"{false_negative_count} objeto(s) nao detectado(s) no periodo.")
    if camera_issue_count:
        add_reason("camera_issue_feedback", "Feedback de problema visual", min(35, camera_issue_count * 12), f"{camera_issue_count} apontamento(s) humano(s).")
    if not events and camera.get("active", True) and latest_frame:
        add_reason("no_events", "Sem eventos no periodo", 8, "Pode ser normal em area de baixo movimento, mas merece conferencia.")

    score = min(100, score)
    status = "inactive" if camera.get("active") is False else "offline" if any(item["kind"] in {"capture_error", "stale_capture", "no_frame"} for item in reasons) else "online"
    return {
        "camera_id": camera_id,
        "camera_name": camera.get("name") or camera_id,
        "active": camera.get("active", True),
        "status": status,
        "severity": camera_health_severity(score),
        "risk_score": score,
        "priority_level": camera.get("priority_level", 3),
        "lat": camera.get("lat"),
        "lon": camera.get("lon"),
        "location": camera.get("location") or camera.get("site") or "",
        "client": camera.get("client") or camera.get("customer") or "",
        "last_frame_at": latest_timestamp,
        "last_frame_age_s": round(last_frame_age_s, 3) if last_frame_age_s is not None else None,
        "capture_age_s": round(capture_age_s, 3) if capture_age_s is not None else None,
        "capture_ms": round(capture_ms, 3),
        "resolve_ms": round(resolve_ms, 3),
        "event_count": len(events),
        "avg_confidence": avg_confidence,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "camera_issue_count": camera_issue_count,
        "reasons": sorted(reasons, key=lambda item: -int(item.get("weight") or 0)),
        "suggested_action": suggest_camera_health_action(reasons, camera),
    }


def camera_health_severity(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "ok"


def suggest_camera_health_action(reasons: list[dict], camera: dict) -> str:
    kinds = {item.get("kind") for item in reasons}
    if "capture_error" in kinds or "stale_capture" in kinds:
        return "Verificar conectividade, credenciais, RTSP/ONVIF e reiniciar a captura da camera."
    if "inactive" in kinds:
        return "Confirmar se a pausa foi intencional; reativar caso a area deva ser monitorada."
    if "no_frame" in kinds or "old_frame" in kinds:
        return "Abrir a camera, validar imagem ao vivo e forcar nova captura."
    if "camera_issue_feedback" in kinds:
        return "Revisar iluminacao, foco, obstrucao, chuva/reflexos e reposicionar se necessario."
    if "false_positives" in kinds or "false_negatives" in kinds or "low_confidence" in kinds:
        return "Revisar limiar de confianca, zonas de interesse e candidatos para fine-tuning desta camera."
    if "high_capture_latency" in kinds or "high_resolve_latency" in kinds:
        return "Preferir substream, reduzir resolucao/FPS ou aproximar o processamento da rede da camera."
    if "no_events" in kinds:
        return "Conferir se a area realmente teve baixo movimento ou se a deteccao esta restritiva demais."
    return "Camera sem acao operacional pendente no periodo analisado."


def seconds_since_iso(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def coerce_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_health_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{round(seconds)}s"
    if seconds < 3600:
        return f"{round(seconds / 60)}min"
    if seconds < 86400:
        return f"{round(seconds / 3600, 1)}h"
    return f"{round(seconds / 86400, 1)}d"


def memory_status() -> dict:
    if psutil:
        mem = psutil.virtual_memory()
        return {
            "total_bytes": mem.total,
            "used_bytes": mem.used,
            "available_bytes": mem.available,
            "used_percent": mem.percent,
        }
    if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return {
            "total_bytes": total,
            "used_bytes": None,
            "available_bytes": None,
            "used_percent": None,
        }
    return {"total_bytes": None, "used_bytes": None, "available_bytes": None, "used_percent": None}


def network_status() -> dict:
    if psutil:
        counters = psutil.net_io_counters()
        return {
            "bytes_sent": counters.bytes_sent,
            "bytes_recv": counters.bytes_recv,
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
            "status": "available",
        }
    return {
        "bytes_sent": None,
        "bytes_recv": None,
        "packets_sent": None,
        "packets_recv": None,
        "status": "psutil unavailable",
    }


def gpu_status() -> dict:
    try:
        import torch
    except Exception:
        return {"available": False, "status": "torch unavailable"}
    if not torch.cuda.is_available():
        return {"available": False, "status": "cuda unavailable"}
    devices = []
    for index in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(index)
        devices.append(
            {
                "index": index,
                "name": props.name,
                "total_memory_bytes": props.total_memory,
                "allocated_bytes": torch.cuda.memory_allocated(index),
                "reserved_bytes": torch.cuda.memory_reserved(index),
            }
        )
    return {"available": True, "devices": devices, "status": "available"}
