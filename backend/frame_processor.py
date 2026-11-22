from __future__ import annotations

import base64
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

import requests

from detector import generate_mock_detection
from stream_resolver import resolve_player
from attribute_extractor import consolidate_track_attributes
from tracker import assign_tracks
from network_security import validate_safe_url

logger = logging.getLogger("kaelis-argos.frame_processor")

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional runtime dependency
    imageio_ffmpeg = None

try:
    from ultralytics import YOLO
except Exception:  # pragma: no cover - optional runtime dependency
    YOLO = None


COCO_TO_OBJECT = {
    0: "pessoa",
    1: "bicicleta",
    2: "carro",
    3: "moto",
    5: "onibus",
    7: "caminhao",
}

YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", "768"))
YOLO_IOU = float(os.getenv("YOLO_IOU", "0.65"))
YOLO_MAX_DET = int(os.getenv("YOLO_MAX_DET", "300"))
PERSON_CONFIDENCE_MIN = float(os.getenv("PERSON_CONFIDENCE_MIN", "0.25"))
YOLO_WARMUP = os.getenv("YOLO_WARMUP", "true").lower() == "true"
FRAME_CAPTURE_TIMEOUT_S = float(os.getenv("FRAME_CAPTURE_TIMEOUT_S", "8"))
ENABLE_MOCK_FALLBACK = os.getenv("ENABLE_MOCK_FALLBACK", "false").lower() == "true"

_models: dict[str, object] = {}
_model_lock = threading.Lock()

DETECTION_PROFILE_DEFAULTS = {
    "near": {"imgsz": 512, "person_confidence_min": 0.35, "iou": 0.60},
    "balanced": {"imgsz": YOLO_IMGSZ, "person_confidence_min": PERSON_CONFIDENCE_MIN, "iou": YOLO_IOU},
    "far": {"imgsz": max(YOLO_IMGSZ, 960), "person_confidence_min": min(PERSON_CONFIDENCE_MIN, 0.20), "iou": 0.70},
}


def build_processed_frame(
    camera: dict,
    history_detections: list,
    include_image: bool = True,
    captured_frame: dict | None = None,
) -> dict:
    benchmark = {
        "resolve_ms": 0.0,
        "capture_ms": 0.0,
        "inference_ms": 0.0,
        "total_ms": 0.0,
    }
    total_started = time.perf_counter()
    if captured_frame and captured_frame.get("frame") is not None:
        source = captured_frame.get("source") or {
            "player_url": camera["original_stream_url"],
            "hls_url": "",
            "poster_url": "",
            "source_type": "memory",
        }
        frame = captured_frame["frame"]
        benchmark["resolve_ms"] = float(captured_frame.get("resolve_ms") or 0)
        benchmark["capture_ms"] = float(captured_frame.get("capture_ms") or 0)
        benchmark["capture_age_ms"] = round((time.monotonic() - float(captured_frame.get("captured_monotonic", time.monotonic()))) * 1000, 3)
        benchmark["capture_source"] = "memory-worker"
        benchmark["capture_worker_mode"] = captured_frame.get("mode", "unknown")
    else:
        try:
            resolve_started = time.perf_counter()
            source = resolve_player(camera["original_stream_url"])
            benchmark["resolve_ms"] = round((time.perf_counter() - resolve_started) * 1000, 3)
        except Exception as exc:
            source = {
                "player_url": camera["original_stream_url"],
                "hls_url": "",
                "poster_url": "",
                "error": str(exc),
            }
        capture_started = time.perf_counter()
        frame = capture_hls_frame(source["hls_url"]) if source["hls_url"] else None
        benchmark["capture_ms"] = round((time.perf_counter() - capture_started) * 1000, 3)
        benchmark["capture_age_ms"] = 0.0
        benchmark["capture_source"] = "inline"
    detector_mode = "loading"
    image_data = ""
    width = camera["frame_width"]
    height = camera["frame_height"]

    if frame is not None:
        height, width = frame.shape[:2]
        if include_image:
            image_data = encode_frame(frame)
        inference_started = time.perf_counter()
        yolo_detections = run_yolo(camera, frame)
        benchmark["inference_ms"] = round((time.perf_counter() - inference_started) * 1000, 3)
        if yolo_detections is not None:
            detector_mode = "yolo"
            detections = yolo_detections
        elif ENABLE_MOCK_FALLBACK:
            detector_mode = "mock-boxes-on-real-frame"
            fallback_detections = ensure_enough_detections(camera, history_detections)
            detections = scale_detections(fallback_detections, camera, width, height)
        else:
            detector_mode = "yolo-unavailable"
            detections = []
    else:
        if include_image:
            poster = fetch_poster(source["poster_url"])
            if poster:
                image_data = poster
        if ENABLE_MOCK_FALLBACK:
            detector_mode = "poster-with-mock-boxes"
            detections = ensure_enough_detections(camera, history_detections)
        else:
            detector_mode = "frame-capture-failed"
            detections = []

    detections = consolidate_track_attributes(assign_tracks(camera, detections, frame))
    benchmark["total_ms"] = round((time.perf_counter() - total_started) * 1000, 3)
    return {
        "camera": camera,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "frame": {
            "width": width,
            "height": height,
            "mode": detector_mode,
            "image": image_data,
        },
        "detections": [item.model_dump(mode="json") for item in detections],
        "benchmark": benchmark,
    }


def build_processed_frames_batch(
    cameras: list[dict],
    histories_by_camera: dict[str, list],
    include_image: bool = True,
    captured_frames: dict[str, dict | None] | None = None,
) -> list[dict]:
    prepared = [
        prepare_frame_payload(
            camera,
            include_image,
            (captured_frames or {}).get(camera["id"]),
        )
        for camera in cameras
    ]
    inferable = [item for item in prepared if item["frame_array"] is not None]
    batch_started = time.perf_counter()
    batch_results = run_yolo_batch(
        [item["camera"] for item in inferable],
        [item["frame_array"] for item in inferable],
    )
    batch_ms = round((time.perf_counter() - batch_started) * 1000, 3) if inferable else 0.0

    output = []
    result_index = 0
    for item in prepared:
        camera = item["camera"]
        frame = item["frame_array"]
        benchmark = item["benchmark"]
        detector_mode = "loading"
        detections = []
        if frame is not None:
            yolo_detections = batch_results[result_index] if batch_results is not None else None
            result_index += 1
            benchmark["inference_ms"] = batch_ms
            benchmark["batch_size"] = len(inferable)
            benchmark["batch_item_ms"] = round(batch_ms / max(1, len(inferable)), 3)
            if yolo_detections is not None:
                detector_mode = "yolo-batch" if len(inferable) > 1 else "yolo"
                detections = yolo_detections
            elif ENABLE_MOCK_FALLBACK:
                detector_mode = "mock-boxes-on-real-frame"
                fallback = ensure_enough_detections(camera, histories_by_camera.get(camera["id"], []))
                detections = scale_detections(fallback, camera, item["width"], item["height"])
            else:
                detector_mode = "yolo-unavailable"
        else:
            detector_mode = item["detector_mode"]
            if ENABLE_MOCK_FALLBACK:
                detections = ensure_enough_detections(camera, histories_by_camera.get(camera["id"], []))

        detections = consolidate_track_attributes(assign_tracks(camera, detections, frame))
        benchmark["total_ms"] = round((time.perf_counter() - item["total_started"]) * 1000, 3)
        output.append(
            {
                "camera": camera,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": item["source"],
                "frame": {
                    "width": item["width"],
                    "height": item["height"],
                    "mode": detector_mode,
                    "image": item["image_data"],
                },
                "detections": [detection.model_dump(mode="json") for detection in detections],
                "benchmark": benchmark,
            }
        )
    return output


def prepare_frame_payload(
    camera: dict,
    include_image: bool = True,
    captured_frame: dict | None = None,
) -> dict:
    benchmark = {
        "resolve_ms": 0.0,
        "capture_ms": 0.0,
        "inference_ms": 0.0,
        "total_ms": 0.0,
        "batch_size": 1,
        "batch_item_ms": 0.0,
    }
    total_started = time.perf_counter()
    if captured_frame and captured_frame.get("frame") is not None:
        source = captured_frame.get("source") or {
            "player_url": camera["original_stream_url"],
            "hls_url": "",
            "poster_url": "",
            "source_type": "memory",
        }
        frame = captured_frame["frame"]
        benchmark["resolve_ms"] = float(captured_frame.get("resolve_ms") or 0)
        benchmark["capture_ms"] = float(captured_frame.get("capture_ms") or 0)
        benchmark["capture_age_ms"] = round((time.monotonic() - float(captured_frame.get("captured_monotonic", time.monotonic()))) * 1000, 3)
        benchmark["capture_source"] = "memory-worker"
        benchmark["capture_worker_mode"] = captured_frame.get("mode", "unknown")
    else:
        try:
            resolve_started = time.perf_counter()
            source = resolve_player(camera["original_stream_url"])
            benchmark["resolve_ms"] = round((time.perf_counter() - resolve_started) * 1000, 3)
        except Exception as exc:
            source = {
                "player_url": camera["original_stream_url"],
                "hls_url": "",
                "poster_url": "",
                "error": str(exc),
            }
        capture_started = time.perf_counter()
        frame = capture_hls_frame(source["hls_url"]) if source["hls_url"] else None
        benchmark["capture_ms"] = round((time.perf_counter() - capture_started) * 1000, 3)
        benchmark["capture_age_ms"] = 0.0
        benchmark["capture_source"] = "inline"

    image_data = ""
    width = camera["frame_width"]
    height = camera["frame_height"]
    detector_mode = "loading"
    if frame is not None:
        height, width = frame.shape[:2]
        if include_image:
            image_data = encode_frame(frame)
    else:
        if include_image:
            poster = fetch_poster(source["poster_url"])
            if poster:
                image_data = poster
        detector_mode = "poster-with-mock-boxes" if ENABLE_MOCK_FALLBACK else "frame-capture-failed"
    return {
        "camera": camera,
        "source": source,
        "frame_array": frame,
        "benchmark": benchmark,
        "total_started": total_started,
        "image_data": image_data,
        "width": width,
        "height": height,
        "detector_mode": detector_mode,
    }


def capture_hls_frame(hls_url: str):
    return capture_hls_frame_with_ffmpeg(hls_url)


def capture_hls_frame_with_ffmpeg(hls_url: str):
    if cv2 is None or np is None or imageio_ffmpeg is None:
        return None
    validate_safe_url(hls_url, context="frame_capture_url")
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-rw_timeout",
        str(int(FRAME_CAPTURE_TIMEOUT_S * 1_000_000)),
        "-i",
        hls_url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=FRAME_CAPTURE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    return image


def encode_frame(frame) -> str:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii")


def fetch_poster(poster_url: str) -> str:
    if not poster_url:
        return ""
    validate_safe_url(poster_url, context="poster_url", allowed_schemes={"http", "https"})
    response = requests.get(poster_url, timeout=10)
    response.raise_for_status()
    return "data:image/jpeg;base64," + base64.b64encode(response.content).decode("ascii")


def run_yolo(camera: dict, frame) -> list:
    if YOLO is None:
        return None
    results = run_yolo_batch([camera], [frame])
    if results is None:
        return None
    return results[0]


def run_yolo_batch(cameras: list[dict], frames: list) -> list[list] | None:
    if YOLO is None or not frames:
        return None
    output: list[list | None] = [None] * len(frames)
    groups: dict[tuple, list[int]] = {}
    for index, camera in enumerate(cameras):
        classes = tuple(camera.get("classes") or [])
        camera_confidence = float(camera.get("confidence_min") or 0.25)
        person_confidence = camera_person_confidence_min(camera)
        effective_confidence = min(camera_confidence, person_confidence) if 0 in classes else camera_confidence
        key = (
            camera_yolo_model(camera),
            camera_yolo_imgsz(camera),
            camera_yolo_iou(camera),
            classes,
            camera_confidence,
            effective_confidence,
        )
        groups.setdefault(key, []).append(index)

    with _model_lock:
        for (model_name, imgsz, iou, classes, camera_confidence, effective_confidence), indexes in groups.items():
            model = get_model(model_name, imgsz)
            batch_frames = [frames[index] for index in indexes]
            results = model.predict(
                batch_frames,
                classes=list(classes),
                conf=effective_confidence,
                device=YOLO_DEVICE,
                imgsz=imgsz,
                iou=iou,
                max_det=YOLO_MAX_DET,
                verbose=False,
            )
            for local_index, result in zip(indexes, results):
                output[local_index] = detections_from_yolo_result(cameras[local_index], frames[local_index], result)
    return output


def get_model(model_name: str | None = None, imgsz: int | None = None):
    selected_model = model_name or YOLO_MODEL
    if selected_model not in _models:
        _models[selected_model] = YOLO(selected_model)
        if YOLO_WARMUP and np is not None:
            warmup_size = int(imgsz or YOLO_IMGSZ)
            warmup = np.zeros((warmup_size, warmup_size, 3), dtype=np.uint8)
            _models[selected_model].predict(warmup, device=YOLO_DEVICE, imgsz=warmup_size, verbose=False)
    return _models[selected_model]


def camera_detection_profile(camera: dict) -> dict:
    profile = str(camera.get("detection_profile") or "balanced")
    return DETECTION_PROFILE_DEFAULTS.get(profile, DETECTION_PROFILE_DEFAULTS["balanced"])


def camera_yolo_model(camera: dict) -> str:
    return str(camera.get("yolo_model") or YOLO_MODEL)


def camera_yolo_imgsz(camera: dict) -> int:
    explicit = camera.get("yolo_imgsz")
    if explicit:
        return int(explicit)
    return int(camera_detection_profile(camera)["imgsz"])


def camera_person_confidence_min(camera: dict) -> float:
    explicit = camera.get("person_confidence_min")
    if explicit is not None:
        return float(explicit)
    return float(camera_detection_profile(camera)["person_confidence_min"])


def camera_yolo_iou(camera: dict) -> float:
    explicit = camera.get("yolo_iou")
    if explicit is not None:
        return float(explicit)
    return float(camera_detection_profile(camera)["iou"])


def detections_from_yolo_result(camera: dict, frame, result) -> list:
    from geoestimator import estimate_geo
    from schemas import Detection

    height, width = frame.shape[:2]
    camera_for_frame = {
        **camera,
        "frame_width": width,
        "frame_height": height,
        "focal_length_px": camera["focal_length_px"] * (width / camera["frame_width"]),
    }
    detections = []
    for box in result.boxes:
        class_id = int(box.cls[0])
        object_type = COCO_TO_OBJECT.get(class_id)
        if not object_type:
            continue
        confidence = float(box.conf[0])
        min_confidence = camera_person_confidence_min(camera) if object_type == "pessoa" else float(camera.get("confidence_min") or 0.25)
        if confidence < min_confidence:
            continue
        x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
        bbox = {"x": x1, "y": y1, "w": max(1, x2 - x1), "h": max(1, y2 - y1)}
        geo = estimate_geo(camera_for_frame, bbox)
        attributes = safe_extract_attributes(frame, object_type, bbox)
        detections.append(
            Detection(
                camera_id=camera["id"],
                camera_name=camera["name"],
                timestamp=datetime.now(timezone.utc),
                object_type=object_type,
                confidence=round(confidence, 2),
                attributes=attributes,
                geo={"lat": geo["lat"], "lon": geo["lon"], "accuracy_m": geo["accuracy_m"]},
                bbox=bbox,
                distance_m=geo["distance_m"],
            )
        )
    return detections


def safe_extract_attributes(frame, object_type: str, bbox: dict) -> dict:
    from attribute_extractor import empty_attributes, extract_attributes

    try:
        from feature_manager import is_feature_enabled

        if not is_feature_enabled("visual_attributes_enabled"):
            return empty_attributes(object_type, "visual_attributes_disabled")
    except Exception:
        logger.exception("could not read visual_attributes_enabled; continuing with object detection")
    try:
        return extract_attributes(frame, {"object_type": object_type, "bbox": bbox})
    except Exception:
        logger.exception("visual attribute extraction failed; keeping object detection result")
        return empty_attributes(object_type, "visual_attribute_extraction_failed")


def scale_detections(detections: list, camera: dict, width: int, height: int) -> list:
    if width == camera["frame_width"] and height == camera["frame_height"]:
        return detections

    scaled = []
    x_scale = width / camera["frame_width"]
    y_scale = height / camera["frame_height"]
    for detection in detections:
        item = detection.model_copy(deep=True)
        item.bbox.x = round(item.bbox.x * x_scale)
        item.bbox.y = round(item.bbox.y * y_scale)
        item.bbox.w = round(item.bbox.w * x_scale)
        item.bbox.h = round(item.bbox.h * y_scale)
        scaled.append(item)
    return scaled


def ensure_enough_detections(camera: dict, detections: list, minimum: int = 4) -> list:
    output = list(detections)
    while len(output) < minimum:
        output.append(generate_mock_detection(camera))
    return output
