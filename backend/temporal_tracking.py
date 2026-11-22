from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


TRACK_STATES = {
    "observed",
    "confirmed",
    "moving",
    "stationary_candidate",
    "stationary",
    "alert",
    "stale",
    "closed",
}
VEHICLE_TYPES = {"carro", "moto", "onibus", "caminhao"}


@dataclass
class TemporalTrackingConfig:
    min_frames_to_confirm_track: int = 3
    min_track_age_seconds_to_confirm: float = 6.0
    stationary_min_seconds: float = 30.0
    stationary_distance_threshold_pixels: float = 18.0
    stationary_distance_threshold_meters: float = 1.5
    same_area_centroid_threshold_pixels: float = 36.0
    same_area_centroid_threshold_meters: float = 3.0
    same_area_iou_threshold: float = 0.25
    geofence_presence_min_seconds: float = 5.0
    tracking_lost_tolerance_seconds: float = 20.0
    max_missing_frames_before_stale: int = 3
    alert_cooldown_seconds: float = 300.0
    history_max_frames: int = 60
    history_max_seconds: float = 900.0
    reassociation_max_seconds: float = 8.0
    reassociation_distance_pixels: float = 42.0


@dataclass
class TrackHistoryEntry:
    timestamp: datetime
    bbox: dict[str, float]
    centroid_image_x: float
    centroid_image_y: float
    lat: float | None = None
    lng: float | None = None
    geofence_id: int | None = None
    detection_confidence: float = 0.0
    object_type: str = ""
    frame_id: int | None = None
    snapshot_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "bbox": self.bbox,
            "centroid_image_x": self.centroid_image_x,
            "centroid_image_y": self.centroid_image_y,
            "lat": self.lat,
            "lng": self.lng,
            "geofence_id": self.geofence_id,
            "detection_confidence": self.detection_confidence,
            "object_type": self.object_type,
            "frame_id": self.frame_id,
            "snapshot_path": self.snapshot_path,
            "metadata": self.metadata,
        }


@dataclass
class TemporalTrack:
    track_id: str
    object_type: str
    camera_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    first_confirmed_at: datetime | None = None
    current_geofence_id: int | None = None
    entered_geofence_at: datetime | None = None
    stationary_candidate_started_at: datetime | None = None
    stationary_started_at: datetime | None = None
    status: str = "observed"
    confidence: float = 0.0
    frames_seen_count: int = 0
    consecutive_frames_seen_count: int = 0
    frames_missing_count: int = 0
    last_position_image: tuple[float, float] | None = None
    current_position_image: tuple[float, float] | None = None
    last_position_map: tuple[float, float] | None = None
    current_position_map: tuple[float, float] | None = None
    history: deque[TrackHistoryEntry] = field(default_factory=deque)
    metadata: dict[str, Any] = field(default_factory=dict)
    closed_at: datetime | None = None
    last_alert_at_by_key: dict[str, datetime] = field(default_factory=dict)

    def seen_duration_seconds(self, now: datetime | None = None) -> float:
        return calculate_seen_duration(self, now)

    def geofence_duration_seconds(self, now: datetime | None = None) -> float:
        return calculate_geofence_duration(self, now)

    def stationary_duration_seconds(self, now: datetime | None = None) -> float:
        return calculate_stationary_duration(self, now)

    def evidence_summary(self, config: TemporalTrackingConfig) -> dict[str, Any]:
        movement_px = calculate_displacement_over_time(self, "pixels")
        movement_m = calculate_displacement_over_time(self, "meters")
        history = list(self.history)
        avg_confidence = sum(item.detection_confidence for item in history) / len(history) if history else 0.0
        geofence_count = sum(1 for item in history if item.geofence_id == self.current_geofence_id and item.geofence_id is not None)
        return {
            "frames_used": len(history),
            "first_frame_at": history[0].timestamp.isoformat() if history else None,
            "last_frame_at": history[-1].timestamp.isoformat() if history else None,
            "average_confidence": round(avg_confidence, 4),
            "movement_pixels": round(movement_px, 3),
            "movement_meters": round(movement_m, 3) if movement_m is not None else None,
            "movement_threshold_pixels": config.stationary_distance_threshold_pixels,
            "movement_threshold_meters": config.stationary_distance_threshold_meters,
            "same_geofence_ratio": round(geofence_count / len(history), 4) if history else 0.0,
            "geofence_id": self.current_geofence_id,
            "decision_reason": self.metadata.get("decision_reason", ""),
        }

    def to_public_dict(self, now: datetime | None = None, config: TemporalTrackingConfig | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        config = config or TemporalTrackingConfig()
        lat = self.current_position_map[0] if self.current_position_map else None
        lng = self.current_position_map[1] if self.current_position_map else None
        return {
            "track_id": self.track_id,
            "object_type": self.object_type,
            "camera_id": self.camera_id,
            "geofence_id": self.current_geofence_id,
            "status": self.status,
            "first_seen_at": self.first_seen_at.isoformat(),
            "first_confirmed_at": self.first_confirmed_at.isoformat() if self.first_confirmed_at else None,
            "last_seen_at": self.last_seen_at.isoformat(),
            "entered_geofence_at": self.entered_geofence_at.isoformat() if self.entered_geofence_at else None,
            "stationary_candidate_started_at": self.stationary_candidate_started_at.isoformat() if self.stationary_candidate_started_at else None,
            "stationary_started_at": self.stationary_started_at.isoformat() if self.stationary_started_at else None,
            "current_position_image": list(self.current_position_image) if self.current_position_image else None,
            "current_position_map": list(self.current_position_map) if self.current_position_map else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "seen_duration_seconds": int(self.seen_duration_seconds(now)),
            "geofence_duration_seconds": int(self.geofence_duration_seconds(now)),
            "stationary_duration_seconds": int(self.stationary_duration_seconds(now)),
            "frames_seen_count": self.frames_seen_count,
            "consecutive_frames_seen_count": self.consecutive_frames_seen_count,
            "frames_missing_count": self.frames_missing_count,
            "confidence": round(self.confidence, 4),
            "lat": lat,
            "lng": lng,
            "risk_level": self.metadata.get("risk_level", "none"),
            "evidence_status": self.metadata.get("evidence_status", "insufficient_temporal_evidence"),
            "decision_reason": self.metadata.get("decision_reason", ""),
            "metadata": self.metadata,
            "history": [item.to_dict() for item in list(self.history)[-10:]],
            "evidence": self.evidence_summary(config),
        }


class TemporalTracker:
    def __init__(self, config: TemporalTrackingConfig | None = None) -> None:
        self.config = config or TemporalTrackingConfig()
        self.tracks: dict[str, TemporalTrack] = {}
        self._generated_counter = 0

    def process_frame(
        self,
        payload: dict[str, Any],
        geofences: list[dict[str, Any]] | None = None,
        frame_id: int | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = parse_timestamp(payload.get("timestamp"))
        camera = payload.get("camera") or {}
        camera_id = str(camera.get("id") or "")
        detections = payload.get("detections") or []
        seen_ids: set[str] = set()
        public_tracks: list[dict[str, Any]] = []
        for detection in detections:
            if detection.get("object_type") not in {"pessoa", *VEHICLE_TYPES, "bicicleta"}:
                continue
            geofence_id = check_geofence_membership(detection, geofences or [])
            track = self.associate_detection_to_track(detection, camera_id, timestamp)
            seen_ids.add(track.track_id)
            update_track_history(track, detection, timestamp, geofence_id, frame_id, self.config)
            update_geofence_entry_time(track, geofence_id, timestamp)
            update_stationary_state(track, timestamp, self.config)
            temporal = track.to_public_dict(timestamp, self.config)
            detection["temporal"] = temporal
            detection["geofence_id"] = geofence_id
            detection["track_id"] = track.track_id
            public_tracks.append(temporal)
        self.mark_missing_tracks(camera_id, timestamp, seen_ids)
        return public_tracks

    def associate_detection_to_track(self, detection: dict[str, Any], camera_id: str, timestamp: datetime) -> TemporalTrack:
        explicit_id = str(detection.get("track_id") or "").strip()
        if explicit_id and explicit_id in self.tracks:
            return self.tracks[explicit_id]
        if explicit_id:
            track = self._new_track(explicit_id, detection, camera_id, timestamp)
            self.tracks[track.track_id] = track
            return track
        candidate = associate_detection_to_track(self.tracks.values(), detection, camera_id, timestamp, self.config)
        if candidate:
            return candidate
        track_id = self._generate_track_id(camera_id, str(detection.get("object_type") or "objeto"))
        track = self._new_track(track_id, detection, camera_id, timestamp)
        self.tracks[track_id] = track
        return track

    def mark_missing_tracks(self, camera_id: str, timestamp: datetime, seen_ids: set[str]) -> None:
        for track in self.tracks.values():
            if track.camera_id != camera_id or track.track_id in seen_ids or track.status == "closed":
                continue
            age_missing = (timestamp - track.last_seen_at).total_seconds()
            if age_missing <= 0:
                continue
            track.frames_missing_count += 1
            if age_missing > self.config.tracking_lost_tolerance_seconds or track.frames_missing_count > self.config.max_missing_frames_before_stale:
                track.status = "stale"
                track.metadata["decision_reason"] = "track_not_seen_beyond_temporal_tolerance"
            if age_missing > self.config.tracking_lost_tolerance_seconds * 2:
                track.status = "closed"
                track.closed_at = timestamp

    def active_tracks(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        return [
            track.to_public_dict(now, self.config)
            for track in self.tracks.values()
            if track.status not in {"closed"}
        ]

    def get_track(self, track_id: str) -> TemporalTrack | None:
        return self.tracks.get(track_id)

    def _new_track(self, track_id: str, detection: dict[str, Any], camera_id: str, timestamp: datetime) -> TemporalTrack:
        confidence = float(detection.get("confidence") or 0.0)
        return TemporalTrack(
            track_id=track_id,
            object_type=str(detection.get("object_type") or ""),
            camera_id=camera_id,
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            confidence=confidence,
            metadata={
                "evidence_status": "insufficient_temporal_evidence",
                "decision_reason": "object_seen_without_previous_temporal_evidence",
                "risk_level": "none",
            },
        )

    def _generate_track_id(self, camera_id: str, object_type: str) -> str:
        self._generated_counter += 1
        return f"{camera_id}:{object_type}:temporal-{self._generated_counter}"


def associate_detection_to_track(
    tracks: Any,
    detection: dict[str, Any],
    camera_id: str,
    timestamp: datetime,
    config: TemporalTrackingConfig,
) -> TemporalTrack | None:
    centroid = bbox_centroid(detection.get("bbox") or {})
    if centroid is None:
        return None
    candidates: list[tuple[float, TemporalTrack]] = []
    for track in tracks:
        if track.camera_id != camera_id or track.object_type != detection.get("object_type") or track.status in {"closed"}:
            continue
        age = (timestamp - track.last_seen_at).total_seconds()
        if age < 0 or age > config.reassociation_max_seconds:
            continue
        if track.current_position_image is None:
            continue
        distance = math.hypot(centroid[0] - track.current_position_image[0], centroid[1] - track.current_position_image[1])
        if distance <= config.reassociation_distance_pixels:
            candidates.append((distance, track))
    candidates.sort(key=lambda item: item[0])
    if len(candidates) != 1:
        return None
    return candidates[0][1]


def update_track_history(
    track: TemporalTrack,
    detection: dict[str, Any],
    timestamp: datetime,
    geofence_id: int | None,
    frame_id: int | None,
    config: TemporalTrackingConfig,
) -> None:
    bbox = normalize_bbox(detection.get("bbox") or {})
    centroid = bbox_centroid(bbox) or (0.0, 0.0)
    geo = detection.get("geo") or {}
    lat = to_float_or_none(geo.get("lat"))
    lng = to_float_or_none(geo.get("lon"))
    track.last_position_image = track.current_position_image
    track.current_position_image = centroid
    track.last_position_map = track.current_position_map
    track.current_position_map = (lat, lng) if lat is not None and lng is not None else None
    track.last_seen_at = timestamp
    track.confidence = float(detection.get("confidence") or 0.0)
    track.frames_seen_count += 1
    track.consecutive_frames_seen_count += 1
    track.frames_missing_count = 0
    track.history.append(
        TrackHistoryEntry(
            timestamp=timestamp,
            bbox=bbox,
            centroid_image_x=centroid[0],
            centroid_image_y=centroid[1],
            lat=lat,
            lng=lng,
            geofence_id=geofence_id,
            detection_confidence=track.confidence,
            object_type=track.object_type,
            frame_id=frame_id,
            snapshot_path=str(detection.get("snapshot_path") or ""),
            metadata={"track_score": detection.get("track_score")},
        )
    )
    prune_history(track, timestamp, config)


def has_enough_temporal_evidence(track: TemporalTrack, now: datetime, config: TemporalTrackingConfig) -> bool:
    age = (now - track.first_seen_at).total_seconds()
    return (
        track.frames_seen_count >= config.min_frames_to_confirm_track
        and age >= config.min_track_age_seconds_to_confirm
        and len(track.history) >= config.min_frames_to_confirm_track
        and track.consecutive_frames_seen_count >= min(2, config.min_frames_to_confirm_track)
    )


def is_track_confirmed(track: TemporalTrack, now: datetime, config: TemporalTrackingConfig) -> bool:
    return has_enough_temporal_evidence(track, now, config)


def has_remained_in_same_area(track: TemporalTrack, config: TemporalTrackingConfig) -> bool:
    history = list(track.history)
    if len(history) < config.min_frames_to_confirm_track:
        return False
    first = history[0]
    last = history[-1]
    centroid_distance = math.hypot(last.centroid_image_x - first.centroid_image_x, last.centroid_image_y - first.centroid_image_y)
    if centroid_distance <= config.same_area_centroid_threshold_pixels:
        return True
    if first.lat is not None and first.lng is not None and last.lat is not None and last.lng is not None:
        if haversine_m(first.lat, first.lng, last.lat, last.lng) <= config.same_area_centroid_threshold_meters:
            return True
    return bbox_iou(first.bbox, last.bbox) >= config.same_area_iou_threshold


def calculate_displacement_over_time(track: TemporalTrack, unit: str = "pixels") -> float:
    history = list(track.history)
    if len(history) < 2:
        return 0.0
    if unit == "meters" and all(item.lat is not None and item.lng is not None for item in history):
        return sum(
            haversine_m(history[index - 1].lat, history[index - 1].lng, history[index].lat, history[index].lng)
            for index in range(1, len(history))
        )
    return sum(
        math.hypot(
            history[index].centroid_image_x - history[index - 1].centroid_image_x,
            history[index].centroid_image_y - history[index - 1].centroid_image_y,
        )
        for index in range(1, len(history))
    )


def has_low_movement_over_time(track: TemporalTrack, config: TemporalTrackingConfig) -> bool:
    movement_m = calculate_displacement_over_time(track, "meters")
    if movement_m and movement_m <= config.stationary_distance_threshold_meters:
        return True
    movement_px = calculate_displacement_over_time(track, "pixels")
    return movement_px <= config.stationary_distance_threshold_pixels


def update_stationary_state(track: TemporalTrack, now: datetime, config: TemporalTrackingConfig) -> TemporalTrack:
    previous_status = track.status
    if not has_enough_temporal_evidence(track, now, config):
        track.status = "observed"
        track.metadata.update(
            evidence_status="insufficient_temporal_evidence",
            decision_reason=f"object_seen_in_{track.frames_seen_count}_frame(s)_without_required_temporal_confirmation",
        )
        return track
    if track.first_confirmed_at is None:
        track.first_confirmed_at = now
    same_area = has_remained_in_same_area(track, config)
    low_movement = has_low_movement_over_time(track, config)
    track.metadata["evidence_status"] = "confirmed_by_previous_frames"
    if same_area and low_movement:
        if track.stationary_candidate_started_at is None:
            track.stationary_candidate_started_at = track.history[0].timestamp if track.history else now
        duration = (now - track.stationary_candidate_started_at).total_seconds()
        if duration >= config.stationary_min_seconds:
            if track.stationary_started_at is None:
                track.stationary_started_at = track.stationary_candidate_started_at
            track.status = "stationary"
            track.metadata["decision_reason"] = "same_object_seen_in_previous_frames_same_area_low_movement"
        else:
            track.status = "stationary_candidate"
            track.metadata["decision_reason"] = "low_movement_detected_but_stationary_min_time_not_reached"
    else:
        clear_stationary_candidate(track)
        track.status = "moving"
        track.metadata["decision_reason"] = "movement_or_area_change_above_threshold"
    return track


def check_geofence_membership(detection: dict[str, Any], geofences: list[dict[str, Any]]) -> int | None:
    geo = detection.get("geo") or {}
    lat = to_float_or_none(geo.get("lat"))
    lon = to_float_or_none(geo.get("lon"))
    if lat is None or lon is None:
        return None
    for geofence in geofences:
        if not geofence.get("active", True):
            continue
        if point_in_polygon(lat, lon, geofence.get("points") or []):
            return int(geofence["id"])
    return None


def update_geofence_entry_time(track: TemporalTrack, geofence_id: int | None, timestamp: datetime) -> None:
    if geofence_id != track.current_geofence_id:
        track.current_geofence_id = geofence_id
        track.entered_geofence_at = timestamp if geofence_id is not None else None


def calculate_seen_duration(track: TemporalTrack, now: datetime | None = None) -> float:
    now = now or track.last_seen_at
    return max(0.0, (now - track.first_seen_at).total_seconds())


def calculate_geofence_duration(track: TemporalTrack, now: datetime | None = None) -> float:
    if not track.entered_geofence_at or track.current_geofence_id is None:
        return 0.0
    now = now or track.last_seen_at
    return max(0.0, (now - track.entered_geofence_at).total_seconds())


def calculate_stationary_duration(track: TemporalTrack, now: datetime | None = None) -> float:
    if track.status not in {"stationary", "alert"} or not track.stationary_started_at:
        return 0.0
    now = now or track.last_seen_at
    return max(0.0, (now - track.stationary_started_at).total_seconds())


def evaluate_geofence_rules(
    tracks: list[TemporalTrack],
    rules: list[dict[str, Any]],
    now: datetime | None = None,
    config: TemporalTrackingConfig | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    config = config or TemporalTrackingConfig()
    events: list[dict[str, Any]] = []
    for track in tracks:
        if track.status == "observed" or not has_enough_temporal_evidence(track, now, config):
            continue
        if track.current_geofence_id is None:
            continue
        for rule in rules:
            event = create_event_if_needed(track, rule, now, config)
            if event:
                events.append(event)
    return events


def create_event_if_needed(
    track: TemporalTrack,
    rule: dict[str, Any],
    now: datetime,
    config: TemporalTrackingConfig,
) -> dict[str, Any] | None:
    if int(rule.get("geofence_id") or -1) != track.current_geofence_id:
        return None
    if not rule.get("active", rule.get("enabled", True)):
        return None
    if not object_type_matches_rule(track.object_type, rule):
        return None
    if not is_rule_active_now(rule, now):
        return None
    min_frames = int(rule.get("min_frames_required") or rule.get("min_frames") or config.min_frames_to_confirm_track)
    if track.frames_seen_count < min_frames:
        return None
    min_confidence = float(rule.get("min_confidence_required") or 0)
    if track.confidence < min_confidence:
        return None
    seen_duration = int(track.seen_duration_seconds(now))
    geofence_duration = int(track.geofence_duration_seconds(now))
    stationary_duration = int(track.stationary_duration_seconds(now))
    max_stationary = int(rule.get("max_stationary_seconds") or 0)
    max_presence = int(rule.get("max_presence_seconds") or 0)
    if max_stationary and stationary_duration <= max_stationary:
        return None
    if max_presence and geofence_duration <= max_presence:
        return None
    if not max_stationary and not max_presence:
        return None
    cooldown = int(rule.get("cooldown_seconds") or config.alert_cooldown_seconds)
    cooldown_key = f"{rule.get('id')}:{track.track_id}:{track.current_geofence_id}:{track.object_type}"
    if is_in_cooldown(track, cooldown_key, now, cooldown):
        return None
    previous_status = track.status
    track.status = "alert"
    severity = str(rule.get("severity") or "medium")
    track.metadata["risk_level"] = severity
    track.metadata["decision_reason"] = "geofence_temporal_rule_satisfied"
    track.last_alert_at_by_key[cooldown_key] = now
    message = format_rule_message(rule, track, stationary_duration, geofence_duration)
    history = list(track.history)
    return {
        "event_type": "temporal_geofence_alert",
        "rule_id": rule.get("id"),
        "track_id": track.track_id,
        "camera_id": track.camera_id,
        "geofence_id": track.current_geofence_id,
        "object_type": track.object_type,
        "severity": severity,
        "message": message,
        "status": "open",
        "seen_duration_seconds": seen_duration,
        "geofence_duration_seconds": geofence_duration,
        "stationary_duration_seconds": stationary_duration,
        "first_evidence_at": history[0].timestamp.isoformat() if history else track.first_seen_at.isoformat(),
        "last_evidence_at": history[-1].timestamp.isoformat() if history else track.last_seen_at.isoformat(),
        "created_at": now.isoformat(),
        "snapshot_path": history[-1].snapshot_path if history else "",
        "frame_reference": history[-1].frame_id if history else None,
        "acknowledged": False,
        "acknowledged_at": "",
        "metadata": {
            "evidence": track.evidence_summary(config),
            "decision": {
                "reason": "same_object_seen_in_previous_frames_same_area_low_movement_or_presence",
                "status_before": previous_status,
                "status_after": "alert",
            },
            "rule": {
                "name": rule.get("name"),
                "max_stationary_seconds": max_stationary,
                "max_presence_seconds": max_presence,
                "cooldown_seconds": cooldown,
            },
        },
    }


def is_in_cooldown(track: TemporalTrack, cooldown_key: str, now: datetime, cooldown_seconds: int) -> bool:
    previous = track.last_alert_at_by_key.get(cooldown_key)
    return bool(previous and (now - previous).total_seconds() < cooldown_seconds)


def format_duration_for_frontend(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}h{minutes:02d}m"
    return f"{minutes:02d}m{secs:02d}s"


def clear_stationary_candidate(track: TemporalTrack) -> None:
    track.stationary_candidate_started_at = None
    track.stationary_started_at = None


def prune_history(track: TemporalTrack, now: datetime, config: TemporalTrackingConfig) -> None:
    while len(track.history) > config.history_max_frames:
        track.history.popleft()
    cutoff = now - timedelta(seconds=config.history_max_seconds)
    while track.history and track.history[0].timestamp < cutoff:
        track.history.popleft()


def normalize_bbox(bbox: dict[str, Any]) -> dict[str, float]:
    return {
        "x": float(bbox.get("x") or 0),
        "y": float(bbox.get("y") or 0),
        "w": max(1.0, float(bbox.get("w") or 1)),
        "h": max(1.0, float(bbox.get("h") or 1)),
    }


def bbox_centroid(bbox: dict[str, Any]) -> tuple[float, float] | None:
    if not bbox:
        return None
    box = normalize_bbox(bbox)
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    a = normalize_bbox(a)
    b = normalize_bbox(b)
    ax2 = a["x"] + a["w"]
    ay2 = a["y"] + a["h"]
    bx2 = b["x"] + b["w"]
    by2 = b["y"] + b["h"]
    ix1 = max(a["x"], b["x"])
    iy1 = max(a["y"], b["y"])
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def point_in_polygon(lat: float, lon: float, points: list[dict[str, Any]]) -> bool:
    if len(points) < 3:
        return False
    inside = False
    j = len(points) - 1
    for index, point in enumerate(points):
        pi = points[index]
        pj = points[j]
        yi = float(pi.get("lat", 0))
        xi = float(pi.get("lon", 0))
        yj = float(pj.get("lat", 0))
        xj = float(pj.get("lon", 0))
        intersects = ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi)
        if intersects:
            inside = not inside
        j = index
    return inside


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def object_type_matches_rule(object_type: str, rule: dict[str, Any]) -> bool:
    object_types = rule.get("object_types")
    if isinstance(object_types, str):
        values = {item.strip() for item in object_types.split(",") if item.strip()}
    elif isinstance(object_types, list):
        values = {str(item) for item in object_types}
    else:
        values = {str(rule.get("object_type") or "")}
    return "todos" in values or object_type in values


def is_rule_active_now(rule: dict[str, Any], now: datetime) -> bool:
    active_days = rule.get("active_days")
    if active_days:
        values = active_days if isinstance(active_days, list) else [item.strip() for item in str(active_days).split(",") if item.strip()]
        weekday = str(now.weekday())
        iso_weekday = str(now.isoweekday())
        if weekday not in values and iso_weekday not in values:
            return False
    start = str(rule.get("active_time_start") or rule.get("start_time") or "00:00")
    end = str(rule.get("active_time_end") or rule.get("end_time") or "23:59")
    current = now.strftime("%H:%M")
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def format_rule_message(rule: dict[str, Any], track: TemporalTrack, stationary_seconds: int, geofence_seconds: int) -> str:
    template = str(rule.get("message_template") or "").strip()
    if template:
        return template.format(
            object_type=track.object_type,
            track_id=track.track_id,
            stationary_duration=format_duration_for_frontend(stationary_seconds),
            geofence_duration=format_duration_for_frontend(geofence_seconds),
        )
    if stationary_seconds:
        return f"{track.object_type.capitalize()} parado ha mais de {format_duration_for_frontend(stationary_seconds)} na area monitorada."
    return f"{track.object_type.capitalize()} permaneceu na area acima do limite configurado."


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def to_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
