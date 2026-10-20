#!/usr/bin/env python3
"""Resumable, page-addressable OCR of the source scans. Originals are read-only.

See sources/OCR.md. Results are unreviewed recognition, never corpus annotations.
--list and --verify need only the Python standard library.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "sources/ocr-plan.json"
OUTPUT = ROOT / "data/ocr"
ALGORITHM = 2


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def page_selection(spec, count):
    if not spec:
        return list(range(1, count + 1))
    selected = set()
    for part in spec.split(","):
        ends = part.split("-")
        if len(ends) > 2:
            raise ValueError(f"Invalid page range: {part}")
        first, last = int(ends[0]), int(ends[-1])
        if first < 1 or first > last or last > count:
            raise ValueError(f"Page range {part} outside 1–{count}")
        selected.update(range(first, last + 1))
    return sorted(selected)


def dependencies():
    local_python = OUTPUT / "tools/python"
    if local_python.exists():
        sys.path.insert(0, str(local_python))
    try:
        import pymupdf
        import PIL
    except ImportError as e:
        raise ValueError("Install scripts/requirements-ocr.txt; see sources/OCR.md") from e
    return pymupdf, PIL


def executable(name):
    local = OUTPUT / "tools/native/usr/bin" / name
    found = str(local) if local.exists() else shutil.which(name)
    if not found:
        raise ValueError(f"Missing {name}; see sources/OCR.md")
    return found


def environment():
    env = os.environ.copy()
    libraries = OUTPUT / "tools/native/usr/lib/x86_64-linux-gnu"
    if libraries.exists():
        env["LD_LIBRARY_PATH"] = str(libraries) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    env["OMP_THREAD_LIMIT"] = "1"
    return env


def run(command, **kwargs):
    result = subprocess.run(command, env=environment(), capture_output=True,
                            timeout=300, **kwargs)
    if result.returncode:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ValueError(f"{Path(command[0]).name}: {stderr.strip()}")
    return result


def verify_original(path):
    receipt_path = path.with_name(path.name + ".receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    actual = digest(path)
    if actual != receipt["sha256"] or path.stat().st_size != receipt["bytes"]:
        raise ValueError(f"Original differs from download receipt: {path}")
    return actual


def inputs(job):
    if job["kind"] == "images":
        items = json.loads((ROOT / job["file_manifest"]).read_text(encoding="utf-8"))
        return [ROOT / "data/texts" / item["path"] for item in items]
    return [ROOT / "data/texts" / job["path"]]


def prepare(job, pymupdf):
    originals = inputs(job)
    hashes = {str(p.relative_to(ROOT)): verify_original(p) for p in originals}
    if job["kind"] == "zip_djvu":
        target = OUTPUT / "inputs" / job["id"] / "source.djvu"
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(originals[0]) as archive:
            members = [i for i in archive.infolist() if i.filename.lower().endswith(".djvu")]
            if len(members) != 1:
                raise ValueError("Expected one DjVu in source archive")
            content = archive.read(members[0])
            extracted_hash = hashlib.sha256(content).hexdigest()
            if target.exists() and digest(target) != extracted_hash:
                raise ValueError("Extracted DjVu changed; preserve it and use a fresh output directory")
            if not target.exists():
                target.write_bytes(content)
            member = members[0].filename
        count = int(run([executable("djvused"), str(target), "-e", "n"], text=True).stdout)
        return originals, hashes, count, {"path": str(target), "archive_member": member,
                                           "sha256": extracted_hash}
    if job["kind"] == "pdf":
        with pymupdf.open(originals[0]) as doc:
            count = len(doc)
        return originals, hashes, count, None
    return originals, hashes, len(originals), None


def word_stats(tsv):
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row.get("text", "").strip():
            words.append((row["text"], float(row["conf"])))
    confidences = [c for _, c in words if c >= 0]
    text = " ".join(t for t, _ in words)
    return {"words": len(words),
            "mean_word_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
            "low_confidence_words": sum(c < 60 for c in confidences),
            "historical_cyrillic_characters": sum(0x460 <= ord(c) <= 0x52f or
                                                   0xa640 <= ord(c) <= 0xa69f or c in "іїІЇ"
                                                   for c in text)}


def cached(record_path, fingerprint):
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["fingerprint"] != fingerprint:
            return False
        required = {record_path.stem + suffix for suffix in
                    (".txt", ".tsv", ".hocr", ".embedded.txt", ".log", ".preview.jpg")}
        if set(record["artifacts"]) != required:
            return False
        return all(digest(record_path.parent / name) == checksum
                   for name, checksum in record["artifacts"].items())
    except (OSError, ValueError, KeyError):
        return False


def scope(job, page):
    for section in job.get("sections", []):
        if section["first"] <= page <= section["last"]:
            return section["role"]
    return "edition_context"


def recognize(task):
    job, page, original, derived, settings, folder, fingerprint = task
    pymupdf, _ = dependencies()
    from PIL import Image, ImageOps
    folder = Path(folder)
    stem = f"{page:04d}"
    record_path = folder / (stem + ".json")
    if cached(record_path, fingerprint):
        return page, "cached"
    with tempfile.TemporaryDirectory(prefix="primarychronicle-ocr-") as temporary:
        temp = Path(temporary)
        embedded = ""
        if job["kind"] == "pdf":
            with pymupdf.open(original) as doc:
                source_page = doc[page - 1]
                embedded = source_page.get_text(sort=False)
                pixmap = source_page.get_pixmap(dpi=settings["dpi"], colorspace=pymupdf.csGRAY)
                pixmap.save(temp / "page.png")
            image = Image.open(temp / "page.png").copy()
        elif job["kind"] == "zip_djvu":
            run([executable("ddjvu"), "-format=pgm", f"-page={page}",
                 f"-scale={settings['dpi']}", derived["path"], str(temp / "page.pgm")])
            image = Image.open(temp / "page.pgm").copy()
            embedded = run([executable("djvutxt"), f"--page={page}", derived["path"]],
                           text=True).stdout
        else:
            image = Image.open(original).convert("L")
        original_size = list(image.size)
        # Do not binarize or erase marks; contrast adjustment is derived only.
        image = ImageOps.autocontrast(image.convert("L"), cutoff=0)
        if job["kind"] == "images" and image.width < 2400:
            image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        image.save(temp / "input.png")
        prefix = temp / stem
        command = [settings["tesseract"], str(temp / "input.png"), str(prefix),
                   "--tessdata-dir", settings["models_dir"], "-l", settings["languages"],
                   "--oem", "1", "--psm", str(settings["psm"]), "--dpi", str(settings["dpi"]),
                   "-c", "tessedit_create_txt=1", "-c", "tessedit_create_tsv=1",
                   "-c", "tessedit_create_hocr=1"]
        result = run(command, text=True)
        # Tesseract sometimes exits successfully after failing one language.
        if "Failed loading language" in result.stderr or "Error opening data file" in result.stderr:
            raise ValueError(result.stderr)
        stats = word_stats(prefix.with_suffix(".tsv").read_text(encoding="utf-8"))
        artifacts = {}
        image.save(folder / (stem + ".preview.jpg"), quality=90)
        artifacts[stem + ".preview.jpg"] = digest(folder / (stem + ".preview.jpg"))
        for suffix in (".txt", ".tsv", ".hocr"):
            source = prefix.with_suffix(suffix)
            target = folder / (stem + suffix)
            shutil.copyfile(source, target.with_suffix(suffix + ".part"))
            target.with_suffix(suffix + ".part").replace(target)
            artifacts[target.name] = digest(target)
        for suffix, text in ((".embedded.txt", embedded), (".log", result.stderr)):
            target = folder / (stem + suffix)
            target.write_text(text, encoding="utf-8")
            artifacts[target.name] = digest(target)
        record = {"schema_version": 1, "fingerprint": fingerprint, "job": job["id"],
                  "page": page, "page_numbering": "one_based_file_or_manifest_order",
                  "source": str(Path(original).relative_to(ROOT)),
                  "archive_member": derived["archive_member"] if derived else None,
                  "scope": scope(job, page), "review_status": "unreviewed",
                  "eligible_for_linguistic_analysis": False, "method": "automatic_ocr",
                  "original_pixel_size": original_size, "ocr_pixel_size": list(image.size),
                  "coordinates": "pixels_in_ocr_image; origin_top_left",
                  "statistics": stats, "artifacts": artifacts, "created_at": now()}
        write_json(record_path, record)
    return page, "recognized"


def summarize(folder, manifest):
    pages = folder / "pages"
    records = []
    for page in manifest["expected_pages"]:
        path = pages / f"{page:04d}.json"
        if cached(path, manifest["fingerprint"]):
            records.append(json.loads(path.read_text(encoding="utf-8")))
    manifest["completed_pages"] = [r["page"] for r in records]
    manifest["complete"] = len(records) == len(manifest["expected_pages"])
    manifest["updated_at"] = now()
    write_json(folder / "run.json", manifest)
    with (folder / "review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["page", "scope", "review_status", "words", "mean_word_confidence",
                         "low_confidence_words", "historical_cyrillic_characters", "text", "layout"])
        for r in records:
            s = r["statistics"]
            writer.writerow([r["page"], r["scope"], r["review_status"], s["words"],
                             s["mean_word_confidence"], s["low_confidence_words"],
                             s["historical_cyrillic_characters"],
                             f"pages/{r['page']:04d}.txt", f"pages/{r['page']:04d}.hocr"])
    with (folder / "text.txt").open("w", encoding="utf-8") as stream:
        for r in records:
            stream.write(f"\n[OCR UNREVIEWED | {r['job']} | file page {r['page']} | {r['scope']}]\n")
            stream.write((pages / f"{r['page']:04d}.txt").read_text(encoding="utf-8"))
            stream.write("\f")
    return len(records)


def process(job, args):
    pymupdf, pil = dependencies()
    originals, source_hashes, count, derived = prepare(job, pymupdf)
    expected = page_selection(job.get("pages"), count)
    selected = page_selection(args.pages, count) if args.pages else expected
    if not set(selected) <= set(expected):
        raise ValueError(f"Requested pages outside OCR scope for {job['id']}")
    languages = args.languages or job["languages"]
    models = Path(args.models_dir).resolve()
    model_hashes = {name: digest(models / (name + ".traineddata")) for name in languages.split("+")}
    tesseract = executable("tesseract")
    version = run([tesseract, "--version"], text=True).stdout.splitlines()[0]
    settings = {"algorithm": ALGORITHM, "dpi": args.dpi, "psm": args.psm,
                "languages": languages, "model_sha256": model_hashes,
                "tesseract_version": version, "pymupdf": pymupdf.__version__,
                "pillow": pil.__version__, "tesseract": tesseract,
                "models_dir": str(models)}
    identity = {"job": job, "sources": source_hashes,
                "settings": {k: v for k, v in settings.items() if k not in ("tesseract", "models_dir")}}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    folder = OUTPUT / job["id"] / fingerprint[:16]
    (folder / "pages").mkdir(parents=True, exist_ok=True)
    lock = folder / ".lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as e:
        raise ValueError(f"Run is locked: {lock}; check the running process before removing a stale lock") from e
    os.close(lock_fd)
    manifest = {"schema_version": 1, "job": job, "fingerprint": fingerprint,
                "sources": source_hashes, "derived_input": derived, "settings": settings,
                "expected_pages": expected, "review_status": "unreviewed",
                "eligible_for_linguistic_analysis": False, "created_at": now(), "errors": []}
    if (folder / "run.json").exists():
        manifest["created_at"] = json.loads((folder / "run.json").read_text())["created_at"]
    write_json(folder.parent / "latest.json", {"run": folder.name})
    summarize(folder, manifest)
    pending = [p for p in selected if not cached(folder / "pages" / f"{p:04d}.json", fingerprint)]
    print(f"{job['id']}: {len(pending)} pending / {len(selected)} selected / {len(expected)} in scope", flush=True)
    try:
        tasks = [(job, page, str(originals[page - 1] if job["kind"] == "images" else originals[0]),
                  derived, settings, str(folder / "pages"), fingerprint) for page in pending]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(recognize, task): task[1] for task in tasks}
            for i, future in enumerate(as_completed(futures), 1):
                page = futures[future]
                try:
                    future.result()
                except Exception as e:
                    manifest["errors"].append({"page": page, "error": str(e)})
                    print(f"ERROR {job['id']} page {page}: {e}", file=sys.stderr, flush=True)
                if i % 20 == 0 or i == len(tasks):
                    print(f"{job['id']}: {i}/{len(tasks)} attempted", flush=True)
    finally:
        try:
            done = summarize(folder, manifest)
            print(f"{job['id']}: {done}/{len(expected)} saved in {folder.relative_to(ROOT)}", flush=True)
        finally:
            lock.unlink(missing_ok=True)
    return bool(manifest["errors"])


def verify(job):
    folder = OUTPUT / job["id"]
    latest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    folder = folder / latest["run"]
    manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    for name, checksum in manifest["sources"].items():
        if digest(ROOT / name) != checksum:
            raise ValueError(f"Changed source: {name}")
    for name, checksum in manifest["settings"]["model_sha256"].items():
        if digest(Path(manifest["settings"]["models_dir"]) / (name + ".traineddata")) != checksum:
            raise ValueError(f"Changed OCR model: {name}")
    if manifest["derived_input"]:
        derived = manifest["derived_input"]
        if digest(derived["path"]) != derived["sha256"]:
            raise ValueError("Changed extracted DjVu")
    missing = [p for p in manifest["expected_pages"]
               if not cached(folder / "pages" / f"{p:04d}.json", manifest["fingerprint"])]
    if missing:
        raise ValueError(f"{len(missing)} missing/corrupt OCR pages; first: {missing[:10]}")
    print(f"Verified {job['id']}: {len(manifest['expected_pages'])} pages; all remain UNREVIEWED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Show OCR/HTR inventory without dependencies")
    parser.add_argument("--verify", action="store_true", help="Check complete outputs, hashes, and source identity offline")
    parser.add_argument("--only", nargs="+", help="Job IDs from sources/ocr-plan.json")
    parser.add_argument("--pages", help="One-based physical pages, e.g. 172-175,358; for one job only")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--psm", type=int, default=3, choices=range(3, 14))
    parser.add_argument("--languages", help="Override Tesseract languages, preserving a separate run")
    parser.add_argument("--models-dir", default=str(OUTPUT / "models"))
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    jobs = plan["jobs"]
    if args.only:
        unknown = set(args.only) - {j["id"] for j in jobs}
        if unknown:
            parser.error("Unknown job IDs: " + ", ".join(sorted(unknown)))
        jobs = [j for j in jobs if j["id"] in args.only]
    if args.list:
        for job in jobs:
            print(f"{job['id']}: {job['status']} | {job.get('pages', 'all physical pages')} | {job['notes']}")
        return 0
    for job in jobs:
        if job["status"] != "ready_for_draft_ocr":
            print(f"{job['id']}: use scripts/htr_sources.py for manuscript recognition", flush=True)
    jobs = [j for j in jobs if j["status"] == "ready_for_draft_ocr"]
    if args.pages and len(jobs) != 1:
        parser.error("--pages requires exactly one OCR job via --only")
    if args.jobs < 1 or args.dpi < 72:
        parser.error("--jobs must be positive and --dpi at least 72")
    if not jobs:
        parser.error("No runnable OCR jobs selected; manuscript HTR remains pending")
    errors = []
    for job in jobs:
        try:
            if args.verify:
                verify(job)
            elif process(job, args):
                errors.append(job["id"] + ": one or more pages failed")
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            errors.append(f"{job['id']}: {e}")
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
