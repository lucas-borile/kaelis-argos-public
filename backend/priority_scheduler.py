from __future__ import annotations

import os
import time
from datetime import datetime, timezone


PRIORITY_SCHEDULER_ENABLED = os.getenv("PRIORITY_SCHEDULER_ENABLED", "true").lower() == "true"
PRIORITY_MAX_CAMERAS_PER_CYCLE = int(os.getenv("PRIORITY_MAX_CAMERAS_PER_CYCLE", "8"))
PRIORITY_FOCUS_TTL_S = float(os.getenv("PRIORITY_FOCUS_TTL_S", "20"))
PRIORITY_ACTIVITY_DECAY_S = float(os.getenv("PRIORITY_ACTIVITY_DECAY_S", "180"))
PRIORITY_EVENT_GRACE_S = float(os.getenv("PRIORITY_EVENT_GRACE_S", "8"))


class PriorityInferenceScheduler:
    def __init__(self) -> None:
        self.focused_cameras: dict[str, float] = {}
        self.camera_events: dict[str, dict] = {}
        self.last_ranked: list[dict] = []

    def set_focus(self, camera_id: str, focused: bool) -> None:
        if focused:
            self.focused_cameras[camera_id] = time.monotonic() + PRIORITY_FOCUS_TTL_S
        else:
            self.focused_cameras.pop(camera_id, None)

    def record_observation(
        self,
        camera_id: str,
        detections_count: int,
        *,
        boost: float = 25.0,
        ttl_s: float = 45.0,
        now: float | None = None,
        reason: str = "event",
    ) -> None:
        now = time.monotonic() if now is None else now
        state = self.camera_events.get(camera_id, {})
        activity_score = float(state.get("activity_score") or 0.0)
        last_seen = float(state.get("last_seen") or now)
        activity_score *= self._decay_factor(now - last_seen)

        if detections_count > 0:
            activity_score = min(100.0, activity_score + 8.0 + detections_count * 4.0)
            self.camera_events[camera_id] = {
                "active": True,
                "boost": max(0.0, float(boost)),
                "expires_at": now + max(5.0, float(ttl_s)),
                "last_seen": now,
                "last_event_at": now,
                "detections_count": detections_count,
                "activity_score": activity_score,
                "reason": reason,
            }
            return

        if state.get("active"):
            state["active"] = False
            state["expires_at"] = min(float(state.get("expires_at") or now), now + min(PRIORITY_EVENT_GRACE_S, max(2.0, float(ttl_s) / 3)))
            state["last_seen"] = now
            state["detections_count"] = 0
            state["activity_score"] = activity_score
            self.camera_events[camera_id] = state

    def rank(
        self,
        cameras: list[dict],
        *,
        now: float,
        latest_frames: dict[str, dict],
        camera_failures: dict[str, int],
        capture_status: dict,
        gate_infos: dict[str, dict],
        due_at_by_camera: dict[str, float],
        has_active_geofence_rules: bool,
        geofence_priority_by_camera: dict[str, int] | None = None,
    ) -> list[dict]:
        self._prune_focus(now)
        self._prune_events(now)
        ranked = []
        geofence_priority_by_camera = geofence_priority_by_camera or {}
        for camera in cameras:
            camera_id = camera["id"]
            latest = latest_frames.get(camera_id) or {}
            detections_count = len(latest.get("detections") or [])
            failures = camera_failures.get(camera_id, 0)
            status = (capture_status.get("cameras") or {}).get(camera_id) or {}
            gate = gate_infos.get(camera_id) or {}
            event_state = self.camera_events.get(camera_id) or {}
            score = 10.0
            reasons = []

            if not PRIORITY_SCHEDULER_ENABLED:
                ranked.append({"camera": camera, "score": score, "reasons": ["disabled"]})
                continue

            priority_level = self._priority_level(camera.get("priority_level", 3))
            score += (priority_level - 3) * 4.5
            reasons.append(f"camera-priority:{priority_level}")

            if status.get("ok"):
                score += 12
                reasons.append("capture-ok")
            else:
                score -= 28
                reasons.append("capture-error")

            if gate.get("decision") == "process":
                score += 22
                reasons.append(f"gate:{gate.get('reason', 'process')}")
            elif gate.get("decision") == "skip":
                score -= 16
                reasons.append("gate:unchanged")

            if has_active_geofence_rules:
                score += 8
                reasons.append("geofence-rules")

            geofence_priority = self._priority_level(geofence_priority_by_camera.get(camera_id, 0), default=0)
            if geofence_priority:
                score += geofence_priority * 4.0
                reasons.append(f"geofence-priority:{geofence_priority}")

            if detections_count:
                score += min(18, detections_count * 4)
                reasons.append("recent-detections")

            if event_state:
                remaining_ratio = max(0.0, min(1.0, (float(event_state.get("expires_at") or now) - now) / max(1.0, float(camera.get("event_boost_ttl_s") or 45))))
                event_boost = min(float(camera.get("event_priority_boost") or 25), float(event_state.get("boost") or 0)) * remaining_ratio
                activity_boost = min(16.0, float(event_state.get("activity_score") or 0) * 0.18)
                if event_boost > 0:
                    score += event_boost
                    reasons.append(f"event-boost:{event_state.get('reason', 'event')}")
                if activity_boost > 0.5:
                    score += activity_boost
                    reasons.append("activity-rate")

            if camera_id in self.focused_cameras:
                score += 30
                reasons.append("operator-focus")

            if failures:
                score -= min(35, failures * 8)
                reasons.append(f"failures:{failures}")

            due_at = float(due_at_by_camera.get(camera_id, now))
            overdue_s = max(0.0, now - due_at)
            if overdue_s:
                score += min(12, overdue_s / 5)
                reasons.append("starvation-guard")

            ranked.append(
                {
                    "camera": camera,
                    "score": round(score, 3),
                    "reasons": reasons,
                    "gate": gate,
                    "detections_count": detections_count,
                    "failures": failures,
                    "event_state": event_state,
                    "priority_level": priority_level,
                }
            )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        self.last_ranked = [
            {
                "camera_id": item["camera"]["id"],
                "score": item["score"],
                "reasons": item["reasons"],
                "gate_decision": item["gate"].get("decision"),
                "gate_reason": item["gate"].get("reason"),
                "detections_count": item["detections_count"],
                "failures": item["failures"],
                "priority_level": item["priority_level"],
                "event_active": bool(item["event_state"].get("active")),
                "event_reason": item["event_state"].get("reason", ""),
            }
            for item in ranked[:20]
        ]
        return ranked

    def select(self, ranked: list[dict]) -> tuple[list[dict], list[dict]]:
        if not PRIORITY_SCHEDULER_ENABLED or PRIORITY_MAX_CAMERAS_PER_CYCLE <= 0:
            return [item["camera"] for item in ranked], []
        selected = ranked[:PRIORITY_MAX_CAMERAS_PER_CYCLE]
        deferred = ranked[PRIORITY_MAX_CAMERAS_PER_CYCLE:]
        return [item["camera"] for item in selected], [item["camera"] for item in deferred]

    def snapshot_status(self) -> dict:
        now = time.monotonic()
        self._prune_focus(now)
        return {
            "enabled": PRIORITY_SCHEDULER_ENABLED,
            "max_cameras_per_cycle": PRIORITY_MAX_CAMERAS_PER_CYCLE,
            "focus_ttl_s": PRIORITY_FOCUS_TTL_S,
            "focused_cameras": sorted(self.focused_cameras.keys()),
            "event_boosted_cameras": sorted(self.camera_events.keys()),
            "last_ranked": self.last_ranked,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _prune_focus(self, now: float) -> None:
        for camera_id, expires_at in list(self.focused_cameras.items()):
            if expires_at <= now:
                self.focused_cameras.pop(camera_id, None)

    def _prune_events(self, now: float) -> None:
        for camera_id, state in list(self.camera_events.items()):
            if float(state.get("expires_at") or 0) <= now and float(state.get("activity_score") or 0) < 1:
                self.camera_events.pop(camera_id, None)
                continue
            last_seen = float(state.get("last_seen") or now)
            state["activity_score"] = float(state.get("activity_score") or 0.0) * self._decay_factor(now - last_seen)
            state["last_seen"] = now
            if float(state.get("expires_at") or 0) <= now and state["activity_score"] < 1:
                self.camera_events.pop(camera_id, None)
            else:
                self.camera_events[camera_id] = state

    def _decay_factor(self, elapsed_s: float) -> float:
        if elapsed_s <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (elapsed_s / max(1.0, PRIORITY_ACTIVITY_DECAY_S))))

    def _priority_level(self, value: object, default: int = 3) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(0 if default == 0 else 1, min(10, number))
