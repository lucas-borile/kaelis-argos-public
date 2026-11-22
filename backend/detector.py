from __future__ import annotations

import random
from datetime import datetime, timezone

from config import CAMERAS
from geoestimator import estimate_geo
from schemas import Detection


OBJECT_TYPES = ["pessoa", "pessoa", "pessoa", "carro", "carro", "moto", "bicicleta"]


def generate_mock_detection(camera: dict | None = None) -> Detection:
    cam = camera or random.choice([item for item in CAMERAS if item["active"]])
    object_type = random.choice(OBJECT_TYPES)
    bbox_h = random.randint(42, 190)
    bbox_w = random.randint(28, 130)
    bbox = {
        "x": random.randint(0, cam["frame_width"] - bbox_w),
        "y": random.randint(80, cam["frame_height"] - bbox_h),
        "w": bbox_w,
        "h": bbox_h,
    }
    geo = estimate_geo(cam, bbox)
    return Detection(
        camera_id=cam["id"],
        camera_name=cam["name"],
        timestamp=datetime.now(timezone.utc),
        object_type=object_type,
        confidence=round(random.uniform(0.62, 0.96), 2),
        geo={
            "lat": geo["lat"],
            "lon": geo["lon"],
            "accuracy_m": geo["accuracy_m"],
        },
        bbox=bbox,
        distance_m=geo["distance_m"],
    )


def generate_scan() -> list[Detection]:
    detections: list[Detection] = []
    for camera in CAMERAS:
        if not camera["active"]:
            continue
        for _ in range(random.randint(1, 5)):
            detections.append(generate_mock_detection(camera))
    return detections
