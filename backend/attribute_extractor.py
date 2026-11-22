from __future__ import annotations

import os
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None


UNKNOWN = "desconhecido"
MULTICOLOR = "multicolorido"
LIGHT = "claro"
DARK = "escuro"
METHOD = "inner_roi_hsv_lab_cluster_v2"
VEHICLE_TYPES = {"carro", "moto", "onibus", "caminhao", "bicicleta"}

ATTRIBUTE_CONFIDENCE_MIN = float(os.getenv("ATTRIBUTE_CONFIDENCE_MIN", "0.48"))
ATTRIBUTE_TRACK_CONFIDENCE_MIN = float(os.getenv("ATTRIBUTE_TRACK_CONFIDENCE_MIN", "0.55"))
ATTRIBUTE_TRACK_HISTORY = int(os.getenv("ATTRIBUTE_TRACK_HISTORY", "8"))
ATTRIBUTE_DEBUG_IMAGES = os.getenv("ATTRIBUTE_DEBUG_IMAGES", "false").lower() == "true"
ATTRIBUTE_DEBUG_DIR = Path(os.getenv("ATTRIBUTE_DEBUG_DIR", "data/debug_attributes"))

_track_attribute_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=ATTRIBUTE_TRACK_HISTORY))


def extract_attributes(frame_bgr, detection: dict[str, Any]) -> dict[str, Any]:
    object_type = detection.get("object_type")
    bbox = detection.get("bbox") or {}
    if object_type == "pessoa":
        return extract_person_attributes(frame_bgr, bbox, detection.get("track_id"))
    if object_type in VEHICLE_TYPES:
        return extract_vehicle_attributes(frame_bgr, bbox)
    return empty_attributes(object_type, "unsupported_object_type")


def extract_person_attributes(frame_bgr, bbox: dict[str, Any], track_id: str | None = None) -> dict[str, Any]:
    quality = compute_crop_quality(frame_bgr, bbox)
    base = empty_attributes("pessoa", quality.get("attribute_warning"))
    base.update(
        {
            "crop_quality": quality["crop_quality"],
            "bbox_area_ratio": quality["bbox_area_ratio"],
            "upper_region_quality": 0.0,
            "lower_region_quality": 0.0,
            "attribute_method": METHOD,
        }
    )
    if quality["crop_quality"] < 0.35 or cv2 is None:
        return with_frame_attributes(base)

    upper = crop_region(frame_bgr, bbox, 0.25, 0.75, 0.18, 0.52)
    lower = crop_region(frame_bgr, bbox, 0.30, 0.70, 0.55, 0.92)
    upper_quality = compute_region_quality(upper, quality["crop_quality"])
    lower_quality = compute_region_quality(lower, quality["crop_quality"])
    upper_result = classify_region_color(upper, upper_quality, ignore_skin=True)
    lower_result = classify_region_color(lower, lower_quality, ignore_skin=False)

    base.update(
        {
            "upper_color": upper_result["color"],
            "upper_color_confidence": upper_result["confidence"],
            "lower_color": lower_result["color"],
            "lower_color_confidence": lower_result["confidence"],
            "upper_region_quality": upper_quality,
            "lower_region_quality": lower_quality,
            "dominant_colors": compact_colors([upper_result["color"], lower_result["color"]]),
            "upper_top_colors": upper_result["top_colors"],
            "lower_top_colors": lower_result["top_colors"],
        }
    )
    warnings = [item for item in [quality.get("attribute_warning"), upper_result.get("warning"), lower_result.get("warning")] if item]
    base["attribute_warning"] = ",".join(sorted(set(warnings))) if warnings else None
    if ATTRIBUTE_DEBUG_IMAGES:
        base["debug_image_path"] = save_person_debug_image(frame_bgr, bbox, upper_result, lower_result, track_id)
    return with_frame_attributes(base)


def extract_vehicle_attributes(frame_bgr, bbox: dict[str, Any]) -> dict[str, Any]:
    quality = compute_crop_quality(frame_bgr, bbox)
    base = empty_attributes("vehicle", quality.get("attribute_warning"))
    base.update(
        {
            "crop_quality": quality["crop_quality"],
            "bbox_area_ratio": quality["bbox_area_ratio"],
            "attribute_method": METHOD,
        }
    )
    crop = crop_object(frame_bgr, bbox, inner_margin=0.12)
    result = classify_region_color(crop, quality["crop_quality"], ignore_skin=False)
    base.update(
        {
            "vehicle_color": result["color"],
            "vehicle_color_confidence": result["confidence"],
            "dominant_colors": compact_colors([result["color"]]),
            "vehicle_top_colors": result["top_colors"],
            "attribute_warning": result.get("warning") or quality.get("attribute_warning"),
        }
    )
    return with_frame_attributes(base)


def consolidate_track_attributes(detections: list) -> list:
    output = []
    for detection in detections:
        attrs = detection.attributes or {}
        if detection.object_type != "pessoa" or not detection.track_id:
            output.append(detection)
            continue
        frame_attrs = attrs.get("frame_attributes") or attrs
        sample = {
            "upper_color": frame_attrs.get("upper_color"),
            "upper_color_confidence": float(frame_attrs.get("upper_color_confidence") or 0),
            "lower_color": frame_attrs.get("lower_color"),
            "lower_color_confidence": float(frame_attrs.get("lower_color_confidence") or 0),
        }
        if has_useful_attribute_sample(sample):
            _track_attribute_history[detection.track_id].append(sample)
        track_attrs = aggregate_track_history(_track_attribute_history[detection.track_id])
        merged = {**attrs, "track_attributes": track_attrs}
        if track_attrs.get("samples", 0) >= 2:
            for key in ("upper_color", "upper_color_confidence", "lower_color", "lower_color_confidence"):
                if track_attrs.get(key) not in (None, UNKNOWN, 0):
                    merged[key] = track_attrs[key]
            merged["dominant_colors"] = compact_colors([merged.get("upper_color"), merged.get("lower_color")])
        output.append(detection.model_copy(update={"attributes": merged}))
    return output


def classify_region_color(region_bgr, region_quality: float = 1.0, ignore_skin: bool = False) -> dict[str, Any]:
    if region_bgr is None or cv2 is None or np is None:
        return color_result(UNKNOWN, 0.0, "region_unavailable")
    if region_bgr.shape[0] < 10 or region_bgr.shape[1] < 10:
        return color_result(UNKNOWN, 0.0, "region_too_small")

    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(float)
    sat = hsv[:, :, 1].astype(float)
    val = hsv[:, :, 2].astype(float)
    valid = (val >= 28) & (val <= 238)
    if ignore_skin:
        skin = (hue >= 0) & (hue <= 25) & (sat >= 35) & (sat <= 175) & (val >= 70) & (val <= 245)
        valid = valid & ~skin
    valid_ratio = float(valid.mean())
    if valid_ratio < 0.18:
        return color_result(UNKNOWN, 0.0, "not_enough_valid_pixels")

    labels = np.full(hue.shape, UNKNOWN, dtype=object)
    neutral = valid & (sat < 42)
    labels[neutral & (val < 72)] = DARK
    labels[neutral & (val > 188)] = LIGHT
    labels[neutral & (val >= 72) & (val <= 188)] = "cinza"
    chromatic = valid & (sat >= 42)
    brown = chromatic & (hue >= 5) & (hue < 28) & (val < 150)
    labels[brown] = "marrom"
    chromatic = chromatic & ~brown
    if chromatic.any():
        labels[chromatic] = np.vectorize(hue_to_color)(hue[chromatic])

    flat = [item for item in labels.ravel().tolist() if item != UNKNOWN]
    if not flat:
        return color_result(UNKNOWN, 0.0, "no_classifiable_pixels")
    counts = Counter(flat)
    total = sum(counts.values())
    ranked = counts.most_common(3)
    first_color, first_count = ranked[0]
    first_ratio = first_count / max(1, total)
    second_ratio = ranked[1][1] / total if len(ranked) > 1 else 0.0
    separation = max(0.0, first_ratio - second_ratio)
    saturation_score = min(1.0, float(sat[valid].mean()) / 95.0)
    neutral_color = first_color in {DARK, LIGHT, "cinza"}
    if neutral_color:
        saturation_score = max(0.55, 1.0 - min(1.0, float(sat[valid].mean()) / 80.0))

    confidence = first_ratio * (0.45 + 0.55 * min(1.0, valid_ratio)) * (0.55 + 0.45 * saturation_score)
    confidence *= 0.60 + 0.40 * min(1.0, separation / 0.35)
    confidence *= max(0.0, min(1.0, region_quality))

    warning = None
    color = first_color
    if first_ratio < 0.34 or (second_ratio > 0.28 and separation < 0.12):
        color = MULTICOLOR
        confidence = min(confidence, 0.42)
        warning = "ambiguous_multicolor_region"
    if color == DARK and confidence < 0.62:
        color = UNKNOWN
        warning = "dark_low_confidence"
    if color == LIGHT and confidence < 0.62:
        color = UNKNOWN
        warning = "light_low_confidence"
    if confidence < ATTRIBUTE_CONFIDENCE_MIN:
        color = UNKNOWN
        warning = warning or "low_color_confidence"
        confidence = 0.0

    return {
        "color": color,
        "confidence": round(float(confidence), 2),
        "top_colors": [{"color": color_name, "score": round(count / total, 2)} for color_name, count in ranked],
        "method": METHOD,
        "warning": warning,
    }


def compute_crop_quality(frame_bgr, bbox: dict[str, Any]) -> dict[str, Any]:
    if frame_bgr is None or cv2 is None or np is None:
        return {"crop_quality": 0.0, "bbox_area_ratio": 0.0, "attribute_warning": "frame_unavailable"}
    frame_h, frame_w = frame_bgr.shape[:2]
    x, y, w, h = bbox_values(bbox)
    area_ratio = min(1.0, max(0.0, (w * h) / max(1, frame_w * frame_h)))
    crop = crop_object(frame_bgr, bbox)
    if crop is None:
        return {"crop_quality": 0.0, "bbox_area_ratio": round(area_ratio, 4), "attribute_warning": "crop_too_small_or_low_quality"}

    crop_h, crop_w = crop.shape[:2]
    size_score = min(1.0, min(crop_h / 96.0, crop_w / 36.0))
    area_score = min(1.0, area_ratio / 0.035)
    aspect = crop_h / max(1, crop_w)
    aspect_score = 1.0 if 1.2 <= aspect <= 5.0 else 0.55
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur_score = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 80.0)
    mean_v = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 2].mean())
    exposure_score = 1.0 - min(1.0, abs(mean_v - 125.0) / 130.0)
    border_cut = x <= 2 or y <= 2 or x + w >= frame_w - 2 or y + h >= frame_h - 2
    border_score = 0.55 if border_cut else 1.0
    quality = (
        size_score * 0.26
        + area_score * 0.16
        + aspect_score * 0.13
        + blur_score * 0.17
        + exposure_score * 0.16
        + border_score * 0.12
    )
    warning = None
    if crop_h < 56 or crop_w < 22:
        warning = "crop_too_small_or_low_quality"
    elif border_cut:
        warning = "bbox_near_frame_border"
    elif exposure_score < 0.35:
        warning = "bad_exposure"
    elif blur_score < 0.22:
        warning = "blurred_crop"
    return {"crop_quality": round(float(quality), 2), "bbox_area_ratio": round(area_ratio, 4), "attribute_warning": warning}


def compute_region_quality(region_bgr, crop_quality: float) -> float:
    if region_bgr is None or cv2 is None or np is None:
        return 0.0
    h, w = region_bgr.shape[:2]
    if h < 10 or w < 10:
        return 0.0
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    valid = (hsv[:, :, 2] >= 28) & (hsv[:, :, 2] <= 238)
    valid_ratio = float(valid.mean())
    size_score = min(1.0, (h * w) / 2200.0)
    quality = (crop_quality * 0.45) + (valid_ratio * 0.35) + (size_score * 0.20)
    return round(float(quality), 2)


def crop_region(frame_bgr, bbox: dict[str, Any], x0: float, x1: float, y0: float, y1: float):
    if frame_bgr is None:
        return None
    frame_h, frame_w = frame_bgr.shape[:2]
    x, y, w, h = bbox_values(bbox)
    rx0 = clamp_int(x + w * x0, 0, frame_w - 1)
    rx1 = clamp_int(x + w * x1, rx0 + 1, frame_w)
    ry0 = clamp_int(y + h * y0, 0, frame_h - 1)
    ry1 = clamp_int(y + h * y1, ry0 + 1, frame_h)
    region = frame_bgr[ry0:ry1, rx0:rx1]
    if region.size == 0:
        return None
    return region


def crop_object(frame_bgr, bbox: dict[str, Any], inner_margin: float = 0.0):
    if frame_bgr is None:
        return None
    frame_h, frame_w = frame_bgr.shape[:2]
    x, y, w, h = bbox_values(bbox)
    margin_x = w * inner_margin
    margin_y = h * inner_margin
    x1 = clamp_int(x + margin_x, 0, frame_w - 1)
    y1 = clamp_int(y + margin_y, 0, frame_h - 1)
    x2 = clamp_int(x + w - margin_x, x1 + 1, frame_w)
    y2 = clamp_int(y + h - margin_y, y1 + 1, frame_h)
    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    return crop


def aggregate_track_history(history: deque[dict[str, Any]]) -> dict[str, Any]:
    return {
        "upper_color": aggregate_color(history, "upper_color", "upper_color_confidence")[0],
        "upper_color_confidence": aggregate_color(history, "upper_color", "upper_color_confidence")[1],
        "lower_color": aggregate_color(history, "lower_color", "lower_color_confidence")[0],
        "lower_color_confidence": aggregate_color(history, "lower_color", "lower_color_confidence")[1],
        "samples": len(history),
    }


def aggregate_color(history: deque[dict[str, Any]], color_key: str, confidence_key: str) -> tuple[str, float]:
    weights: Counter[str] = Counter()
    for sample in history:
        color = sample.get(color_key)
        confidence = float(sample.get(confidence_key) or 0)
        if color in {None, UNKNOWN, MULTICOLOR} or confidence < 0.4:
            continue
        weights[color] += confidence
    if not weights:
        return UNKNOWN, 0.0
    color, score = weights.most_common(1)[0]
    total = sum(weights.values())
    confidence = min(0.95, (score / max(0.01, total)) * min(1.0, total / 2.2))
    if confidence < ATTRIBUTE_TRACK_CONFIDENCE_MIN:
        return UNKNOWN, 0.0
    return color, round(float(confidence), 2)


def has_useful_attribute_sample(sample: dict[str, Any]) -> bool:
    return any(
        sample.get(color_key) not in {None, UNKNOWN, MULTICOLOR} and sample.get(confidence_key, 0) >= 0.4
        for color_key, confidence_key in [("upper_color", "upper_color_confidence"), ("lower_color", "lower_color_confidence")]
    )


def with_frame_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    frame_keys = [
        "upper_color",
        "upper_color_confidence",
        "lower_color",
        "lower_color_confidence",
        "vehicle_color",
        "vehicle_color_confidence",
        "dominant_colors",
        "crop_quality",
        "bbox_area_ratio",
        "upper_region_quality",
        "lower_region_quality",
        "attribute_method",
        "attribute_warning",
    ]
    attributes["frame_attributes"] = {key: attributes.get(key) for key in frame_keys if key in attributes}
    return attributes


def empty_attributes(object_type: str | None, warning: str | None = None) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "upper_color": UNKNOWN if object_type == "pessoa" else None,
        "upper_color_confidence": 0.0 if object_type == "pessoa" else None,
        "lower_color": UNKNOWN if object_type == "pessoa" else None,
        "lower_color_confidence": 0.0 if object_type == "pessoa" else None,
        "vehicle_color": UNKNOWN if object_type in VEHICLE_TYPES else None,
        "vehicle_color_confidence": 0.0 if object_type in VEHICLE_TYPES else None,
        "dominant_colors": [],
        "crop_quality": 0.0,
        "bbox_area_ratio": 0.0,
        "attribute_method": METHOD,
        "attribute_warning": warning,
    }


def color_result(color: str, confidence: float, warning: str | None = None) -> dict[str, Any]:
    return {"color": color, "confidence": confidence, "top_colors": [], "method": METHOD, "warning": warning}


def hue_to_color(hue: float) -> str:
    if hue < 5 or hue >= 170:
        return "vermelho"
    if hue < 13:
        return "laranja"
    if hue < 28:
        return "amarelo"
    if hue < 88:
        return "verde"
    if hue < 122:
        return "azul"
    if hue < 145:
        return "roxo"
    if hue < 160:
        return "rosa"
    return "vermelho"


def compact_colors(colors: list[str | None]) -> list[str]:
    output = []
    for color in colors:
        if color and color not in {UNKNOWN, MULTICOLOR} and color not in output:
            output.append(color)
    return output


def save_person_debug_image(frame_bgr, bbox: dict[str, Any], upper: dict[str, Any], lower: dict[str, Any], track_id: str | None) -> str:
    if cv2 is None or frame_bgr is None:
        return ""
    ATTRIBUTE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    image = frame_bgr.copy()
    x, y, w, h = bbox_values(bbox)
    draw_rect(image, x, y, w, h, (255, 255, 255), "bbox")
    draw_relative_rect(image, bbox, 0.25, 0.75, 0.18, 0.52, (0, 255, 255), f"upper {upper['color']} {upper['confidence']}")
    draw_relative_rect(image, bbox, 0.30, 0.70, 0.55, 0.92, (255, 255, 0), f"lower {lower['color']} {lower['confidence']}")
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}_{(track_id or 'no_track').replace(':', '_')}.jpg"
    path = ATTRIBUTE_DEBUG_DIR / name
    cv2.imwrite(str(path), image)
    return str(path)


def draw_relative_rect(image, bbox: dict[str, Any], x0: float, x1: float, y0: float, y1: float, color, label: str) -> None:
    x, y, w, h = bbox_values(bbox)
    rx = int(x + w * x0)
    ry = int(y + h * y0)
    rw = int(w * (x1 - x0))
    rh = int(h * (y1 - y0))
    draw_rect(image, rx, ry, rw, rh, color, label)


def draw_rect(image, x: int, y: int, w: int, h: int, color, label: str) -> None:
    cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
    cv2.putText(image, label, (x, max(14, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def bbox_values(bbox: dict[str, Any]) -> tuple[int, int, int, int]:
    return int(bbox.get("x", 0)), int(bbox.get("y", 0)), max(1, int(bbox.get("w", 1))), max(1, int(bbox.get("h", 1)))


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
