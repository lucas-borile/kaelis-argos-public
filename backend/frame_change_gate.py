from __future__ import annotations

import os
import threading
import time

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None


FRAME_CHANGE_GATE_ENABLED = os.getenv("FRAME_CHANGE_GATE_ENABLED", "true").lower() == "true"
FRAME_CHANGE_THRESHOLD = float(os.getenv("FRAME_CHANGE_THRESHOLD", "0.018"))
FRAME_CHANGE_PIXEL_THRESHOLD = float(os.getenv("FRAME_CHANGE_PIXEL_THRESHOLD", "0.012"))
FRAME_CHANGE_PIXEL_DELTA = int(os.getenv("FRAME_CHANGE_PIXEL_DELTA", "18"))
FRAME_CHANGE_FORCE_INTERVAL_S = float(os.getenv("FRAME_CHANGE_FORCE_INTERVAL_S", "45"))
FRAME_CHANGE_DOWNSCALE_WIDTH = int(os.getenv("FRAME_CHANGE_DOWNSCALE_WIDTH", "160"))


class FrameChangeGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}

    def should_process(self, camera: dict, captured_frame: dict | None) -> tuple[bool, dict]:
        if not FRAME_CHANGE_GATE_ENABLED:
            return True, {"enabled": False, "decision": "process", "reason": "disabled"}
        if cv2 is None or np is None:
            return True, {"enabled": True, "decision": "process", "reason": "cv2-unavailable"}
        if not captured_frame or captured_frame.get("frame") is None:
            return True, {"enabled": True, "decision": "process", "reason": "no-frame"}

        camera_id = camera["id"]
        frame_small = self._prepare_frame(captured_frame["frame"])
        if frame_small is None:
            return True, {"enabled": True, "decision": "process", "reason": "prepare-failed"}

        now = time.monotonic()
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                return True, {"enabled": True, "decision": "process", "reason": "first-frame"}

            age_s = now - float(state.get("processed_monotonic", 0.0))
            if age_s >= FRAME_CHANGE_FORCE_INTERVAL_S:
                return True, {
                    "enabled": True,
                    "decision": "process",
                    "reason": "force-interval",
                    "last_processed_age_s": round(age_s, 3),
                }

            previous = state.get("frame_small")
            if previous is None or previous.shape != frame_small.shape:
                return True, {"enabled": True, "decision": "process", "reason": "shape-changed"}

            diff = cv2.absdiff(previous, frame_small)
            mean_delta = float(np.mean(diff) / 255.0)
            changed_ratio = float(np.mean(diff >= FRAME_CHANGE_PIXEL_DELTA))
            changed = mean_delta >= FRAME_CHANGE_THRESHOLD or changed_ratio >= FRAME_CHANGE_PIXEL_THRESHOLD
            return changed, {
                "enabled": True,
                "decision": "process" if changed else "skip",
                "reason": "changed" if changed else "unchanged",
                "mean_delta": round(mean_delta, 5),
                "changed_ratio": round(changed_ratio, 5),
                "threshold": FRAME_CHANGE_THRESHOLD,
                "pixel_threshold": FRAME_CHANGE_PIXEL_THRESHOLD,
                "last_processed_age_s": round(age_s, 3),
            }

    def mark_processed(self, camera_id: str, captured_frame: dict | None) -> None:
        if not FRAME_CHANGE_GATE_ENABLED or cv2 is None or np is None:
            return
        if not captured_frame or captured_frame.get("frame") is None:
            return
        frame_small = self._prepare_frame(captured_frame["frame"])
        if frame_small is None:
            return
        with self._lock:
            self._states[camera_id] = {
                "frame_small": frame_small,
                "processed_monotonic": time.monotonic(),
                "captured_frame_index": captured_frame.get("frame_index"),
            }

    def snapshot_status(self) -> dict:
        with self._lock:
            return {
                "enabled": FRAME_CHANGE_GATE_ENABLED,
                "tracked_cameras": len(self._states),
                "threshold": FRAME_CHANGE_THRESHOLD,
                "pixel_threshold": FRAME_CHANGE_PIXEL_THRESHOLD,
                "pixel_delta": FRAME_CHANGE_PIXEL_DELTA,
                "force_interval_s": FRAME_CHANGE_FORCE_INTERVAL_S,
                "downscale_width": FRAME_CHANGE_DOWNSCALE_WIDTH,
            }

    @staticmethod
    def _prepare_frame(frame):
        if frame is None or cv2 is None:
            return None
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            return None
        target_width = max(32, FRAME_CHANGE_DOWNSCALE_WIDTH)
        target_height = max(18, int(height * (target_width / width)))
        resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)
