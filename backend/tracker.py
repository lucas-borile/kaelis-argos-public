from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timezone
from typing import Any


TRACK_MAX_AGE_S = float(os.getenv("TRACK_MAX_AGE_S", "30"))
TRACK_IOU_MIN = float(os.getenv("TRACK_IOU_MIN", "0.18"))
TRACK_GEO_MAX_M = float(os.getenv("TRACK_GEO_MAX_M", "35"))
PERSON_TRACK_MAX_AGE_S = float(os.getenv("PERSON_TRACK_MAX_AGE_S", "18"))
PERSON_TRACK_MIN_SCORE = float(os.getenv("PERSON_TRACK_MIN_SCORE", "0.52"))
PERSON_GEO_MAX_M = float(os.getenv("PERSON_GEO_MAX_M", "22"))
PERSON_CENTER_MAX_PX = float(os.getenv("PERSON_CENTER_MAX_PX", "120"))
PERSON_SIZE_RATIO_MIN = float(os.getenv("PERSON_SIZE_RATIO_MIN", "0.45"))

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None

_lock = threading.Lock()
_tracks_by_camera: dict[str, dict[str, Any]] = {}
_next_ids_by_camera: dict[str, int] = {}


def assign_tracks(camera: dict, detections: list, frame=None) -> list:
    camera_id = camera["id"]
    now = datetime.now(timezone.utc)
    with _lock:
        state = _tracks_by_camera.setdefault(camera_id, {})
        prune_old_tracks(state, now)
        output = []
        used_tracks: set[str] = set()
        for detection in detections:
            appearance = crop_signature(frame, detection.bbox.model_dump()) if frame is not None else None
            match = match_track(state, detection, used_tracks, appearance, now)
            track_id = match["track_id"] if match else None
            track_score = match["score"] if match else 1.0
            if track_id is None:
                track_id = create_track_id(camera_id, detection.object_type)
            used_tracks.add(track_id)
            state[track_id] = {
                "object_type": detection.object_type,
                "bbox": detection.bbox.model_dump(),
                "geo": detection.geo.model_dump(),
                "appearance": appearance,
                "last_seen": now,
            }
            output.append(detection.model_copy(update={"track_id": track_id, "track_score": round(track_score, 2)}))
        return output


def prune_old_tracks(state: dict[str, Any], now: datetime) -> None:
    expired = [
        track_id
        for track_id, track in state.items()
        if (now - track["last_seen"]).total_seconds() > TRACK_MAX_AGE_S
    ]
    for track_id in expired:
        state.pop(track_id, None)


def match_track(
    state: dict[str, Any],
    detection,
    used_tracks: set[str],
    appearance,
    now: datetime,
) -> dict[str, Any] | None:
    best_track_id = None
    best_score = 0.0
    bbox = detection.bbox.model_dump()
    geo = detection.geo.model_dump()
    for track_id, track in state.items():
        if track_id in used_tracks or track["object_type"] != detection.object_type:
            continue
        age_s = (now - track["last_seen"]).total_seconds()
        max_age = PERSON_TRACK_MAX_AGE_S if detection.object_type == "pessoa" else TRACK_MAX_AGE_S
        if age_s > max_age:
            continue
        overlap = bbox_iou(bbox, track["bbox"])
        geo_distance = haversine_m(geo["lat"], geo["lon"], track["geo"]["lat"], track["geo"]["lon"])
        geo_limit = PERSON_GEO_MAX_M if detection.object_type == "pessoa" else TRACK_GEO_MAX_M
        if overlap < TRACK_IOU_MIN and geo_distance > geo_limit:
            continue

        center_distance = bbox_center_distance(bbox, track["bbox"])
        size_score = bbox_size_score(bbox, track["bbox"])
        appearance_score = compare_signatures(appearance, track.get("appearance"))
        if detection.object_type == "pessoa":
            if center_distance > PERSON_CENTER_MAX_PX or size_score < PERSON_SIZE_RATIO_MIN:
                continue
            geo_score = max(0.0, 1.0 - (geo_distance / geo_limit))
            center_score = max(0.0, 1.0 - (center_distance / PERSON_CENTER_MAX_PX))
            score = (overlap * 0.22) + (geo_score * 0.28) + (center_score * 0.18) + (size_score * 0.12) + (appearance_score * 0.20)
            if score < PERSON_TRACK_MIN_SCORE:
                continue
        else:
            geo_score = max(0.0, 1.0 - (geo_distance / geo_limit))
            score = (overlap * 0.7) + (geo_score * 0.3)
        if score > best_score:
            best_score = score
            best_track_id = track_id
    if best_track_id is None:
        return None
    return {"track_id": best_track_id, "score": best_score}


def create_track_id(camera_id: str, object_type: str) -> str:
    key = f"{camera_id}:{object_type}"
    next_id = _next_ids_by_camera.get(key, 1)
    _next_ids_by_camera[key] = next_id + 1
    return f"{camera_id}:{object_type}:{next_id}"


def bbox_iou(a: dict[str, float], b: dict[str, float]) -> float:
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    area_a = max(1.0, a["w"] * a["h"])
    area_b = max(1.0, b["w"] * b["h"])
    return intersection / (area_a + area_b - intersection)


def bbox_center_distance(a: dict[str, float], b: dict[str, float]) -> float:
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    return math.hypot(ax - bx, ay - by)


def bbox_size_score(a: dict[str, float], b: dict[str, float]) -> float:
    area_a = max(1.0, a["w"] * a["h"])
    area_b = max(1.0, b["w"] * b["h"])
    return min(area_a, area_b) / max(area_a, area_b)


def crop_signature(frame, bbox: dict[str, float]):
    if cv2 is None or np is None or frame is None:
        return None
    height, width = frame.shape[:2]
    x1 = max(0, min(width - 1, int(bbox["x"])))
    y1 = max(0, min(height - 1, int(bbox["y"])))
    x2 = max(x1 + 1, min(width, int(bbox["x"] + bbox["w"])))
    y2 = max(y1 + 1, min(height, int(bbox["y"] + bbox["h"])))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [12, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def compare_signatures(current, previous) -> float:
    if current is None or previous is None or np is None:
        return 0.5
    distance = float(np.linalg.norm(current - previous))
    return max(0.0, min(1.0, 1.0 - (distance / 1.4)))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = max(0.0, min(1.0, a))
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
