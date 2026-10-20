#!/usr/bin/env python3
"""Download the source catalog without changing existing originals.

Requires Python 3 and curl. Receipts (URLs, dates, sizes, SHA-256) stay with
the local data; --verify checks those receipts without network access.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "sources/catalog.json"
DEST = ROOT / "data/texts"


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate(path, kind, encoding):
    content = path.read_bytes()
    if kind == "pdf":
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
            raise ValueError("Not a complete PDF")
    elif kind == "jpeg":
        if not content.startswith(b"\xff\xd8") or not content.endswith(b"\xff\xd9"):
            raise ValueError("Not a complete JPEG")
    elif kind == "json":
        json.loads(content)
    elif kind == "zip":
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Corrupt ZIP")
    elif kind == "html":
        text = content.decode(encoding)
        if "<html" not in text.lower() or len(text) < 500:
            raise ValueError("Not an HTML source document")
        if not any("А" <= char <= "я" for char in text):
            raise ValueError("No Cyrillic text in HTML source")
        if any(marker in text.lower() for marker in
               ("just a moment...", "cf-chl-", "verify you are human")):
            raise ValueError("Challenge page instead of source")
    elif not content:
        raise ValueError("Empty download")


def fetch(item):
    path = DEST / item["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = path.with_name(path.name + ".receipt.json")
    if path.exists():
        if not receipt_path.exists():
            raise ValueError(f"Existing file has no receipt: {path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sha256"] != digest(path) or receipt["url"] != item["url"]:
            raise ValueError(f"Existing source differs: {path}")
        validate(path, item["kind"], item.get("encoding", "utf-8"))
        return f"OK existing {item['path']}"
    temporary = path.with_name(path.name + ".part")
    result = subprocess.run([
        "curl", "-sSL", "--fail", "--retry", "2", "--connect-timeout", "20",
        "--max-time", "180", "--output", str(temporary),
        "--write-out", "%{url_effective}", item["url"],
    ], capture_output=True, text=True)
    if result.returncode:
        temporary.unlink(missing_ok=True)
        raise ValueError(result.stderr.strip())
    try:
        validate(temporary, item["kind"], item.get("encoding", "utf-8"))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.rename(path)
    receipt = {
        "url": item["url"], "effective_url": result.stdout,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "bytes": path.stat().st_size, "sha256": digest(path),
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return f"Downloaded {item['path']} ({receipt['bytes']} bytes)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", help="Source IDs from sources/catalog.json")
    parser.add_argument("--verify", action="store_true", help="Verify local files offline")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    sources = catalog["sources"]
    if args.only:
        unknown = set(args.only) - {source["id"] for source in sources}
        if unknown:
            parser.error("Unknown source IDs: " + ", ".join(sorted(unknown)))
        sources = [source for source in sources if source["id"] in args.only]
    files = []
    for source in sources:
        files.extend(source["files"])
        if source.get("file_manifest"):
            files.extend(json.loads((ROOT / source["file_manifest"]).read_text(encoding="utf-8")))
    if len({item["path"] for item in files}) != len(files):
        parser.error("Duplicate destination paths")
    errors = []
    if args.verify:
        for item in files:
            try:
                path = DEST / item["path"]
                receipt = json.loads(path.with_name(path.name + ".receipt.json").read_text())
                if receipt["url"] != item["url"]:
                    raise ValueError("URL differs")
                if path.stat().st_size != receipt["bytes"]:
                    raise ValueError("Size differs")
                if digest(path) != receipt["sha256"]:
                    raise ValueError("SHA-256 differs")
                validate(path, item["kind"], item.get("encoding", "utf-8"))
            except Exception as error:
                errors.append(f"{item['path']}: {error}")
        print(f"Verified {len(files) - len(errors)}/{len(files)} files")
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [(item, pool.submit(fetch, item)) for item in files]
            for item, future in futures:
                try:
                    print(future.result(), flush=True)
                except Exception as error:
                    errors.append(f"{item['path']}: {error}")
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
