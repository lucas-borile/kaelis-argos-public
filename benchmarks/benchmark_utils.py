from __future__ import annotations

import csv
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: str | Path) -> Path:
    item = Path(path)
    item.parent.mkdir(parents=True, exist_ok=True)
    return item


def list_images(source: str | Path) -> list[Path]:
    root = Path(source)
    if root.is_file() and root.suffix.lower() in IMAGE_SUFFIXES:
        return [root]
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def file_size_kb(path: str | Path) -> float:
    try:
        return round(Path(path).stat().st_size / 1024, 3)
    except OSError:
        return 0.0


def percentile(values: list[float], pct: float) -> float:
    clean = sorted(float(value) for value in values if value is not None and not math.isnan(float(value)))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return clean[int(rank)]
    return clean[lower] * (upper - rank) + clean[upper] * (rank - lower)


def average(values: list[float]) -> float:
    clean = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    return statistics.mean(clean) if clean else 0.0


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = ensure_parent(path)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = ensure_parent(path)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = ensure_parent(path)
    fields = sorted({key for row in rows for key in row.keys()})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: serialize_cell(row.get(key)) for key in fields})


def serialize_cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def rows_from_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_classes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


def summarize_records(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    started_at: str,
    ended_at: str,
    duration_seconds: float,
) -> dict[str, Any]:
    frames_processed = len(rows)
    error_rows = [row for row in rows if row.get("status") != "ok"]
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    inference = [float(row.get("inference_latency_ms") or 0) for row in ok_rows]
    total_latency = [float(row.get("total_latency_ms") or row.get("inference_latency_ms") or 0) for row in ok_rows]
    queue_lag = [float(row.get("queue_lag_ms") or 0) for row in rows]
    queue_sizes = [float(row.get("queue_size_at_process") or row.get("queue_size_at_enqueue") or 0) for row in rows]
    cpu_values = [float(row.get("cpu_percent") or row.get("cpu_after_percent") or 0) for row in rows]
    ram_values = [float(row.get("ram_mb") or row.get("ram_after_mb") or 0) for row in rows]
    image_sizes = [float(row.get("image_kb") or 0) for row in rows]
    detection_counts = [int(float(row.get("detections_count") or 0)) for row in rows]
    class_counter: Counter[str] = Counter()
    camera_summary = per_camera_summary(rows)
    for row in rows:
        class_counter.update(parse_classes(row.get("classes")))

    cameras = int(config.get("cameras") or len(camera_summary) or 1)
    interval_seconds = float(config.get("interval_seconds") or config.get("interval") or 1)
    demand_fps = cameras / interval_seconds if interval_seconds else 0
    measured_fps = frames_processed / duration_seconds if duration_seconds > 0 else 0
    safe_capacity_fps = measured_fps * 0.5
    frames_expected = int(config.get("frames_expected") or frames_processed)
    max_queue_size = max(queue_sizes) if queue_sizes else 0
    queue_growth_detected = bool(max_queue_size > frames_expected * 0.1) if frames_expected else False
    viability_status = viability(demand_fps, measured_fps, safe_capacity_fps)
    frames_with_detection = sum(1 for count in detection_counts if count > 0)
    detection_frame_rate = frames_with_detection / frames_processed if frames_processed else 0
    avg_image_kb = average(image_sizes)
    avg_inference_seconds = average(inference) / 1000 if inference else 0
    monthly = monthly_estimates(cameras, interval_seconds, avg_image_kb, detection_frame_rate, avg_inference_seconds)

    return {
        "test_config": config,
        "timing": {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": round(duration_seconds, 3),
        },
        "processing_summary": {
            "frames_expected": frames_expected,
            "frames_processed": frames_processed,
            "frames_error": len(error_rows),
            "error_rate_percent": round((len(error_rows) / frames_processed) * 100, 3) if frames_processed else 0.0,
            "demand_fps": round(demand_fps, 4),
            "measured_fps": round(measured_fps, 4),
            "safe_capacity_fps": round(safe_capacity_fps, 4),
            "viability_status": viability_status,
        },
        "latency_summary": {
            "avg_latency_ms": round(average(total_latency or inference), 3),
            "avg_inference_latency_ms": round(average(inference), 3),
            "median_latency_ms": round(percentile(total_latency or inference, 0.5), 3),
            "p95_latency_ms": round(percentile(total_latency or inference, 0.95), 3),
            "p99_latency_ms": round(percentile(total_latency or inference, 0.99), 3),
            "avg_queue_lag_ms": round(average(queue_lag), 3),
            "max_queue_lag_ms": round(max(queue_lag), 3) if queue_lag else 0,
        },
        "queue_summary": {
            "max_queue_size": round(max_queue_size, 3),
            "avg_queue_size": round(average(queue_sizes), 3),
            "queue_growth_detected": queue_growth_detected,
        },
        "resource_summary": {
            "avg_cpu_percent": round(average(cpu_values), 3),
            "max_cpu_percent": round(max(cpu_values), 3) if cpu_values else 0,
            "avg_ram_mb": round(average(ram_values), 3),
            "max_ram_mb": round(max(ram_values), 3) if ram_values else 0,
        },
        "detections_summary": {
            "total_detections": sum(detection_counts),
            "avg_detections_per_frame": round(average(detection_counts), 3),
            "frames_with_detection": frames_with_detection,
            "detection_frame_rate_percent": round(detection_frame_rate * 100, 3),
            "detections_by_class": dict(class_counter),
        },
        "monthly_estimates": monthly,
        "camera_summary": camera_summary,
    }


def per_camera_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("camera_id") or "local")].append(row)
    output = []
    for camera_id, items in sorted(grouped.items()):
        latencies = [float(row.get("inference_latency_ms") or 0) for row in items if row.get("status") == "ok"]
        detections = [int(float(row.get("detections_count") or 0)) for row in items]
        queues = [float(row.get("queue_size_at_process") or row.get("queue_size_at_enqueue") or 0) for row in items]
        output.append(
            {
                "camera_id": camera_id,
                "frames_processed": len(items),
                "frames_error": sum(1 for row in items if row.get("status") != "ok"),
                "detections_total": sum(detections),
                "avg_latency_ms": round(average(latencies), 3),
                "p95_latency_ms": round(percentile(latencies, 0.95), 3),
                "max_queue_size": round(max(queues), 3) if queues else 0,
            }
        )
    return output


def monthly_estimates(
    cameras: int,
    interval_seconds: float,
    avg_image_kb: float,
    detection_frame_rate: float,
    avg_inference_seconds: float,
) -> dict[str, Any]:
    frames_per_day = cameras * 86400 / interval_seconds if interval_seconds else 0
    frames_per_month = frames_per_day * 30
    avg_image_mb = avg_image_kb / 1024
    processed_gb_per_month = frames_per_month * avg_image_mb / 1024
    stored_gb_per_month = processed_gb_per_month * detection_frame_rate
    inference_hours_per_month = frames_per_month * avg_inference_seconds / 3600
    return {
        "frames_per_month": round(frames_per_month),
        "processed_gb_per_month": round(processed_gb_per_month, 3),
        "stored_gb_per_month": round(stored_gb_per_month, 3),
        "inference_hours_per_month": round(inference_hours_per_month, 3),
    }


def viability(demand_fps: float, measured_fps: float, safe_capacity_fps: float) -> str:
    if safe_capacity_fps >= demand_fps:
        return "viável"
    if measured_fps >= demand_fps:
        return "limite"
    return "não viável"


def class_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(parse_classes(row.get("classes")))
    return counter


def ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000
