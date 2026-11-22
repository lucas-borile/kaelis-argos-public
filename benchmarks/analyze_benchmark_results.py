from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from benchmark_utils import read_jsonl, rows_from_csv, summarize_records, write_json
from report_generator import generate_html_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analisa resultados CSV/JSONL de benchmark do Kaelis Argos.")
    parser.add_argument("--input", required=True, help="Arquivo .csv ou .jsonl produzido pelos benchmarks.")
    parser.add_argument("--out", default="./benchmarks/results/analysis_summary.json")
    parser.add_argument("--report", default="./benchmarks/reports/analysis_report.html")
    parser.add_argument("--cameras", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--duration-seconds", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if input_path.suffix.lower() == ".jsonl":
        rows = read_jsonl(input_path)
    elif input_path.suffix.lower() == ".csv":
        rows = rows_from_csv(input_path)
    else:
        raise SystemExit("Use um arquivo .csv ou .jsonl")

    if not rows:
        raise SystemExit("Arquivo sem registros para analisar")

    config = infer_config(rows, args, input_path)
    started_at = str(rows[0].get("timestamp") or rows[0].get("started_at") or "")
    ended_at = str(rows[-1].get("timestamp") or rows[-1].get("ended_at") or "")
    duration_seconds = args.duration_seconds or infer_duration_seconds(rows)
    summary = summarize_records(rows, config, started_at, ended_at, duration_seconds)
    write_json(args.out, summary)
    generate_html_report(rows, summary, args.report, "analysis")
    print(f"Summary: {args.out}")
    print(f"Report: {args.report}")
    print(f"Status: {summary['processing_summary']['viability_status']}")
    return 0


def infer_config(rows: list[dict], args: argparse.Namespace, input_path: Path) -> dict:
    first = rows[0]
    cameras = args.cameras or count_cameras(rows)
    interval = args.interval or float(first.get("interval_seconds") or first.get("interval") or 1)
    duration_seconds = args.duration_seconds or infer_duration_seconds(rows)
    return {
        "input": str(input_path),
        "cameras": cameras,
        "interval_seconds": interval,
        "duration_seconds": round(duration_seconds, 3),
        "frames_expected": int(cameras * max(1, round(duration_seconds / interval))) if interval else len(rows),
        "model": first.get("model") or first.get("yolo_model") or "",
        "device": first.get("device") or first.get("yolo_device") or "",
        "imgsz": first.get("imgsz") or first.get("yolo_imgsz") or "",
        "conf": first.get("conf") or first.get("confidence_min") or "",
    }


def count_cameras(rows: list[dict]) -> int:
    return max(1, len({str(row.get("camera_id") or "local") for row in rows}))


def infer_duration_seconds(rows: list[dict]) -> float:
    if not rows:
        return 1.0
    timestamps = [str(row.get("timestamp") or "") for row in rows if row.get("timestamp")]
    if len(timestamps) >= 2:
        from datetime import datetime

        try:
            start = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
            return max(1.0, (end - start).total_seconds())
        except ValueError:
            pass
    total_latency_ms = sum(float(row.get("total_latency_ms") or row.get("inference_latency_ms") or 0) for row in rows)
    return max(1.0, total_latency_ms / 1000)


if __name__ == "__main__":
    raise SystemExit(main())
