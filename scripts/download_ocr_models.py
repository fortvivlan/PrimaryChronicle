#!/usr/bin/env python3
"""Fetch pinned OCR models into data/ocr/models, or verify them offline."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from download_sources import digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    models = json.loads((ROOT / "sources/ocr-models.json").read_text(encoding="utf-8"))["models"]
    folder = ROOT / "data/ocr/models"
    folder.mkdir(parents=True, exist_ok=True)
    errors = []
    for model in models:
        path = folder / model["filename"]
        try:
            if not path.exists() and not args.verify:
                temporary = path.with_suffix(path.suffix + ".part")
                result = subprocess.run(["curl", "-sSL", "--fail", "--retry", "2",
                                         "--connect-timeout", "20", "--max-time", "300",
                                         "--output", str(temporary), model["url"]],
                                        capture_output=True, text=True)
                if result.returncode:
                    raise ValueError(result.stderr.strip())
                if digest(temporary) != model["sha256"] or temporary.stat().st_size != model["bytes"]:
                    raise ValueError("Downloaded model does not match pinned checksum/size")
                temporary.rename(path)
            if path.stat().st_size != model["bytes"] or digest(path) != model["sha256"]:
                raise ValueError("Existing model differs; preserve it separately before downloading again")
            receipt = path.with_name(path.name + ".receipt.json")
            if not args.verify and not receipt.exists():
                receipt.write_text(json.dumps({**model, "verified_at": datetime.now(timezone.utc).isoformat()},
                                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("Verified " + model["id"])
        except (OSError, ValueError) as e:
            errors.append(f"{model['id']}: {e}")
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
