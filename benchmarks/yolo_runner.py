from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None

try:
    from ultralytics import YOLO
except Exception:  # pragma: no cover
    YOLO = None


class YoloRunner:
    def __init__(self, model: str, device: str, imgsz: int, conf: float) -> None:
        if YOLO is None:
            raise RuntimeError("ultralytics is not installed")
        self.model_name = model
        self.device = device
        self.imgsz = imgsz
        self.conf = conf
        self.model = YOLO(model)

    def infer_image(self, image_path: str | Path) -> dict[str, Any]:
        if cv2 is None:
            raise RuntimeError("opencv-python is not installed")
        path = Path(image_path)
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"failed to read image: {path}")
        return self.infer_array(image)

    def infer_array(self, image) -> dict[str, Any]:
        started = time.perf_counter()
        results = self.model.predict(
            image,
            conf=self.conf,
            device=self.device,
            imgsz=self.imgsz,
            verbose=False,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        classes: list[str] = []
        detections_count = 0
        for result in results:
            names = result.names or {}
            for box in result.boxes:
                detections_count += 1
                class_id = int(box.cls[0])
                classes.append(str(names.get(class_id, class_id)))
        return {
            "inference_latency_ms": round(latency_ms, 3),
            "detections_count": detections_count,
            "classes": classes,
        }


def cpu_percent() -> float:
    if not psutil:
        return 0.0
    value = psutil.cpu_percent(interval=None)
    return round(float(value), 3)


def ram_mb() -> float:
    if not psutil:
        return 0.0
    return round(float(psutil.Process().memory_info().rss) / (1024 * 1024), 3)
