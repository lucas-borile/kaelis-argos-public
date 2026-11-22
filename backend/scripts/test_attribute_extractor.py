from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from attribute_extractor import extract_person_attributes


def parse_bbox(value: str) -> dict:
    parts = [int(item.strip()) for item in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    return {"x": x1, "y": y1, "w": max(1, x2 - x1), "h": max(1, y2 - y1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Testa extracao de atributos visuais em uma bbox de pessoa.")
    parser.add_argument("--image", required=True, help="Caminho do frame/imagem.")
    parser.add_argument("--bbox", required=True, type=parse_bbox, help="BBox no formato x1,y1,x2,y2.")
    args = parser.parse_args()

    frame = cv2.imread(args.image)
    if frame is None:
        raise SystemExit(f"Nao foi possivel abrir a imagem: {args.image}")

    attributes = extract_person_attributes(frame, args.bbox)
    print(json.dumps(attributes, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
