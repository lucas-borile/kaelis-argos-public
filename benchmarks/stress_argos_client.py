from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from benchmark_utils import (
    ensure_parent,
    file_size_kb,
    list_images,
    summarize_records,
    utc_now_iso,
    write_json,
)
from report_generator import generate_html_report
from yolo_runner import YoloRunner, cpu_percent, ram_mb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test de cliente Kaelis Argos com câmeras simuladas.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--cameras", type=int, required=True)
    parser.add_argument("--interval", type=float, required=True)
    parser.add_argument("--duration-minutes", type=float, required=True)
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", default="./benchmarks/results/stress_results.jsonl")
    parser.add_argument("--summary", default="./benchmarks/results/stress_summary.json")
    parser.add_argument("--report", default="./benchmarks/reports/stress_report.html")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    images = list_images(args.source)
    if not images:
        raise SystemExit(f"Nenhuma imagem encontrada em {args.source}")
    started_at = utc_now_iso()
    wall_start = time.perf_counter()
    duration_seconds = args.duration_minutes * 60
    test_end = wall_start + duration_seconds
    work_queue: queue.Queue[dict] = queue.Queue()
    rows: list[dict] = []
    rows_lock = threading.Lock()
    writer_lock = threading.Lock()
    stop_event = threading.Event()
    out_path = ensure_parent(args.out)

    config = {
        "source": args.source,
        "cameras": args.cameras,
        "interval_seconds": args.interval,
        "duration_minutes": args.duration_minutes,
        "model": args.model,
        "device": args.device,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "workers": args.workers,
        "frames_expected": int(args.cameras * max(1, math.ceil(duration_seconds / args.interval))),
    }

    with out_path.open("w", encoding="utf-8") as handle:
        producers = [
            threading.Thread(
                target=producer,
                args=(camera_index, images, args.interval, test_end, work_queue),
                daemon=True,
            )
            for camera_index in range(args.cameras)
        ]
        for item in producers:
            item.start()

        with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
            futures = [
                executor.submit(worker, index, args, work_queue, stop_event, rows, rows_lock, handle, writer_lock)
                for index in range(max(args.workers, 1))
            ]
            for item in producers:
                item.join()
            work_queue.join()
            stop_event.set()
            for future in futures:
                future.result()

    ended_at = utc_now_iso()
    real_duration_seconds = time.perf_counter() - wall_start
    summary = summarize_records(rows, config, started_at, ended_at, real_duration_seconds)
    write_json(args.summary, summary)
    generate_html_report(rows, summary, args.report, "stress test")
    print(f"JSONL: {args.out}")
    print(f"Summary: {args.summary}")
    print(f"Report: {args.report}")
    return 0


def producer(camera_index: int, images: list[Path], interval: float, test_end: float, work_queue: queue.Queue) -> None:
    camera_id = f"cam_{camera_index + 1:03d}"
    frame_number = 0
    next_at = time.perf_counter()
    while time.perf_counter() < test_end:
        now = time.perf_counter()
        if now < next_at:
            time.sleep(min(0.05, next_at - now))
            continue
        image_path = images[(frame_number + camera_index) % len(images)]
        frame_number += 1
        item = {
            "camera_id": camera_id,
            "frame_id": f"{camera_id}_{int(time.time())}_{frame_number:04d}",
            "image_path": image_path,
            "enqueued_at": time.perf_counter(),
            "timestamp": utc_now_iso(),
            "queue_size_at_enqueue": work_queue.qsize(),
        }
        work_queue.put(item)
        next_at += interval


def worker(
    worker_index: int,
    args: argparse.Namespace,
    work_queue: queue.Queue,
    stop_event: threading.Event,
    rows: list[dict],
    rows_lock: threading.Lock,
    handle,
    writer_lock: threading.Lock,
) -> None:
    try:
        runner = YoloRunner(args.model, args.device, args.imgsz, args.conf)
    except Exception as exc:
        runner = None
        init_error = str(exc)

    while not stop_event.is_set() or not work_queue.empty():
        try:
            item = work_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        process_started = time.perf_counter()
        queue_lag_ms = (process_started - item["enqueued_at"]) * 1000
        image_path = Path(item["image_path"])
        row = {
            "timestamp": utc_now_iso(),
            "camera_id": item["camera_id"],
            "frame_id": item["frame_id"],
            "source": "simulated",
            "image_path": str(image_path),
            "image_kb": file_size_kb(image_path),
            "queue_size_at_enqueue": item["queue_size_at_enqueue"],
            "queue_size_at_process": work_queue.qsize(),
            "queue_lag_ms": round(queue_lag_ms, 3),
            "model": args.model,
            "device": args.device,
            "imgsz": args.imgsz,
            "conf": args.conf,
            "worker_id": worker_index,
            "status": "ok",
            "error": None,
        }
        if runner is None:
            row.update(
                {
                    "status": "error",
                    "error": init_error,
                    "inference_latency_ms": 0,
                    "detections_count": 0,
                    "classes": [],
                }
            )
        else:
            try:
                row.update(runner.infer_image(image_path))
            except Exception as exc:
                row.update(
                    {
                        "status": "error",
                        "error": str(exc),
                        "inference_latency_ms": 0,
                        "detections_count": 0,
                        "classes": [],
                    }
                )
        row["total_latency_ms"] = round((time.perf_counter() - item["enqueued_at"]) * 1000, 3)
        row["cpu_percent"] = cpu_percent()
        row["ram_mb"] = ram_mb()
        row["saved_to_storage"] = bool(row.get("detections_count", 0))
        with rows_lock:
            rows.append(row)
        with writer_lock:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
        work_queue.task_done()


if __name__ == "__main__":
    raise SystemExit(main())
