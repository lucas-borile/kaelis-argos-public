from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a Kaelis Argos YOLO candidate model from a curated dataset.")
    parser.add_argument("--data", required=True, help="Path to YOLO data.yaml")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"Dataset data.yaml not found: {data_path}")

    try:
        from ultralytics import YOLO
    except Exception as exc:
        raise SystemExit(f"Ultralytics is not installed or failed to import: {exc}") from exc

    project_dir = Path(args.project)
    project_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "data": str(data_path),
        "model": args.model,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": args.device,
    }
    (project_dir / "training_request.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    model = YOLO(args.model)
    results = model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(project_dir),
        name="run",
        exist_ok=True,
    )
    print(results)


if __name__ == "__main__":
    main()
