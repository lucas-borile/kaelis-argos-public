from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a curated Kaelis Argos feedback dataset scaffold.")
    parser.add_argument("--metadata", required=True, help="Path to metadata.json exported by /feedback/export-dataset")
    parser.add_argument("--output", required=True, help="Output dataset directory")
    args = parser.parse_args()

    metadata_path = Path(args.metadata)
    output_dir = Path(args.output)
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dataset_manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# Dataset candidato\n\nGerado a partir de feedback aprovado. Revise privacidade, splits e labels antes de treinar.\n",
        encoding="utf-8",
    )
    print(f"Dataset scaffold written to {output_dir}")


if __name__ == "__main__":
    main()
