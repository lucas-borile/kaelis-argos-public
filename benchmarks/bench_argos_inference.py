from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from benchmark_utils import file_size_kb, list_images, summarize_records, utc_now_iso, write_csv, write_json
from report_generator import generate_html_report
from yolo_runner import YoloRunner, cpu_percent, ram_mb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark local de inferência YOLO para Kaelis Argos.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", default="./benchmarks/results/inference_results.csv")
    parser.add_argument("--report", default="./benchmarks/reports/inference_report.html")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = utc_now_iso()
    wall_start = time.perf_counter()
    images = list_images(args.source)
    rows = []
    try:
        runner = YoloRunner(args.model, args.device, args.imgsz, args.conf)
    except Exception as exc:
        runner = None
        init_error = str(exc)
    else:
        init_error = ""

    for repeat in range(1, max(args.repeat, 1) + 1):
        for image_path in images:
            cpu_before = cpu_percent()
            ram_before = ram_mb()
            row = {
                "timestamp": utc_now_iso(),
                "camera_id": "local",
                "frame_id": f"local_{repeat}_{image_path.stem}",
                "image_path": str(image_path),
                "repeat": repeat,
                "status": "ok",
                "error": None,
                "image_kb": file_size_kb(image_path),
                "cpu_before_percent": cpu_before,
                "ram_before_mb": ram_before,
                "model": args.model,
                "device": args.device,
                "imgsz": args.imgsz,
                "conf": args.conf,
                "queue_size_at_enqueue": 0,
                "queue_size_at_process": 0,
                "queue_lag_ms": 0,
            }
            if runner is None:
                row.update(
                    {
                        "status": "error",
                        "error": init_error,
                        "inference_latency_ms": 0,
                        "total_latency_ms": 0,
                        "detections_count": 0,
                        "classes": [],
                    }
                )
            else:
                total_start = time.perf_counter()
                try:
                    result = runner.infer_image(image_path)
                    row.update(result)
                    row["total_latency_ms"] = round((time.perf_counter() - total_start) * 1000, 3)
                except Exception as exc:
                    row.update(
                        {
                            "status": "error",
                            "error": str(exc),
                            "inference_latency_ms": 0,
                            "total_latency_ms": round((time.perf_counter() - total_start) * 1000, 3),
                            "detections_count": 0,
                            "classes": [],
                        }
                    )
            row["cpu_after_percent"] = cpu_percent()
            row["ram_after_mb"] = ram_mb()
            row["cpu_percent"] = row["cpu_after_percent"]
            row["ram_mb"] = row["ram_after_mb"]
            row["saved_to_storage"] = bool(row.get("detections_count", 0))
            rows.append(row)

    ended_at = utc_now_iso()
    duration_seconds = time.perf_counter() - wall_start
    config = {
        "source": args.source,
        "model": args.model,
        "device": args.device,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "repeat": args.repeat,
        "cameras": 1,
        "interval_seconds": max(duration_seconds / len(rows), 1) if rows else 1,
        "workers": 1,
        "frames_expected": len(images) * max(args.repeat, 1),
    }
    summary = summarize_records(rows, config, started_at, ended_at, duration_seconds)
    out_path = Path(args.out)
    summary_path = out_path.with_name(f"{out_path.stem}_summary.json")
    write_csv(out_path, rows)
    write_json(summary_path, summary)
    generate_html_report(rows, summary, args.report, "inferência local")
    print(f"CSV: {out_path}")
    print(f"Summary: {summary_path}")
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
