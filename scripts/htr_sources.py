#!/usr/bin/env python3
"""Historical Cyrillic recognition with Kraken; keep raw, unreviewed line records.

Run with Python 3.10–3.13 and scripts/requirements-htr.txt. No remote OCR APIs.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
from importlib.metadata import version
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import traceback

from ocr_sources import (OUTPUT, PLAN, ROOT, digest, inputs, now, page_selection,
                         scope, verify_original, write_json)

SCOPES = {"andrew-egor162": "1-136", "kirik-2011": "312-341,358-412,474-534",
          "pchela-1893": "75-518", "josephus-1958": "172-476"}
WORKER = {}


def cached(path, fingerprint):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        required = {path.stem + suffix for suffix in (".txt", ".alto.xml", ".lines.json", ".preview.jpg")}
        return (record["fingerprint"] == fingerprint and set(record["artifacts"]) == required and
                all(digest(path.parent / name) == checksum for name, checksum in record["artifacts"].items()))
    except (OSError, ValueError, KeyError):
        return False


def initialize(settings):
    print(f"HTR worker {os.getpid()}: loading models", flush=True)
    import torch
    from kraken.tasks import RecognitionTaskModel, SegmentationTaskModel
    from kraken.configs import RecognitionInferenceConfig, SegmentationInferenceConfig
    torch.set_num_threads(settings["threads"])
    torch.set_num_interop_threads(1)
    accelerator = "gpu" if settings.get("device", "cpu").startswith("cuda:") else "cpu"
    devices = [int(settings["device"].split(":")[1])] if accelerator == "gpu" else 1
    if accelerator == "gpu":
        torch.cuda.set_device(devices[0])
    WORKER.update({"recognizer": RecognitionTaskModel.load_model(settings["model"]),
                   "segmenter": SegmentationTaskModel.load_model(settings["segmentation_model"]),
                   "rec_config": RecognitionInferenceConfig(device=devices, accelerator=accelerator,
                                                            batch_size=settings["batch_size"],
                                                            num_line_workers=0, num_threads=settings["threads"],
                                                            raise_on_error=True),
                   "seg_config": SegmentationInferenceConfig(device=devices, accelerator=accelerator,
                                                             num_threads=settings["threads"], raise_on_error=True)})
    print(f"HTR worker {os.getpid()}: ready", flush=True)


def release_gpu_cache(settings):
    # Independent workers share VRAM. Keep weights, but return unused activation
    # buffers after each prediction instead of retaining each worker's peak.
    if settings.get("device", "cpu").startswith("cuda:"):
        import torch
        torch.cuda.empty_cache()


def recognize(task):
    from PIL import Image, ImageOps
    from kraken.serialization import serialize
    from shapely.errors import GEOSException
    job, page, source, settings, folder, fingerprint = task
    folder = Path(folder)
    stem = f"{page:04d}"
    path = folder / (stem + ".json")
    if cached(path, fingerprint):
        return page
    started = time.perf_counter()
    if job["kind"] == "pdf":
        import pymupdf
        with pymupdf.open(source) as doc:
            pix = doc[page - 1].get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    else:
        image = Image.open(source).convert("RGB")
    original_size = list(image.size)
    if image.width > settings["max_width"]:
        scale = settings["max_width"] / image.width
        image = image.resize((settings["max_width"], round(image.height * scale)), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image)
    print(f"HTR {job['id']} page {page}: segmenting", flush=True)
    primary_image = image
    segmentation_warnings = []
    partial_segmentation = False
    for scale in (1, 0.9, 0.8):
        image = primary_image if scale == 1 else primary_image.resize(
            (round(primary_image.width * scale), round(primary_image.height * scale)), Image.Resampling.LANCZOS)
        try:
            bounds = WORKER["segmenter"].predict(image, WORKER["seg_config"])
            break
        except Exception as e:
            geometry_error = (isinstance(e, (GEOSException, IndexError)) or
                              str(e).startswith("Invalid bounding polygon") or
                              isinstance(e, NotImplementedError) and "multi-part geometries" in str(e))
            if not geometry_error:
                raise
            segmentation_warnings.append({"scale": scale, "error": str(e)})
            if scale == 0.8:
                # Preserve usable lines on damaged/complex pages. This fallback
                # is explicitly marked partial; it never becomes reviewed text.
                partial_segmentation = True
                WORKER["seg_config"].raise_on_error = False
                try:
                    bounds = WORKER["segmenter"].predict(image, WORKER["seg_config"])
                finally:
                    WORKER["seg_config"].raise_on_error = True
                print(f"HTR page {page}: PARTIAL segmentation; manual line review required", flush=True)
                break
            print(f"HTR page {page}: retrying invalid geometry at a smaller raster", flush=True)
        finally:
            release_gpu_cache(settings)
    print(f"HTR {job['id']} page {page}: recognizing {len(bounds.lines)} lines", flush=True)
    try:
        predictions = list(WORKER["recognizer"].predict(image, bounds, WORKER["rec_config"]))
    finally:
        release_gpu_cache(settings)
    result = replace(bounds, lines=predictions, imagename=stem + ".preview.jpg")
    lines = [{"id": line.id, "text": line.prediction,
              "baseline": [[int(x), int(y)] for x, y in line.baseline],
              "boundary": [[int(x), int(y)] for x, y in line.boundary],
              "character_confidences": [float(c) for c in line.confidences]}
             for line in predictions]
    text = "\n".join(line["text"] for line in lines) + "\n"
    (folder / (stem + ".txt")).write_text(text, encoding="utf-8")
    write_json(folder / (stem + ".lines.json"), {"coordinates": "pixels_in_preview; origin_top_left",
                                                "reading_order": "automatic_unreviewed", "lines": lines})
    (folder / (stem + ".alto.xml")).write_text(serialize(result, image_size=image.size,
                                                       template="alto", sub_line_segmentation=False), encoding="utf-8")
    image.save(folder / (stem + ".preview.jpg"), quality=90)
    artifacts = {stem + suffix: digest(folder / (stem + suffix))
                 for suffix in (".txt", ".alto.xml", ".lines.json", ".preview.jpg")}
    confidences = [c for line in lines for c in line["character_confidences"]]
    stats = {"words": len(text.split()), "lines": len(lines), "mean_word_confidence": None,
             "mean_character_confidence": round(100 * sum(confidences) / len(confidences), 2) if confidences else None,
             "historical_cyrillic_characters": sum(0x460 <= ord(c) <= 0x52f or
                                                    0xa640 <= ord(c) <= 0xa69f or c in "іїІЇ" for c in text)}
    write_json(path, {"schema_version": 1, "fingerprint": fingerprint, "job": job["id"], "page": page,
                      "source": Path(source).relative_to(ROOT).as_posix(), "method": "automatic_htr",
                      "scope": "manuscript_transcription_candidate" if job["id"] == "andrew-egor162" else scope(job, page),
                      "review_status": "unreviewed", "eligible_for_linguistic_analysis": False,
                      "original_pixel_size": original_size, "ocr_pixel_size": list(image.size),
                      "segmentation_warnings": segmentation_warnings,
                      "partial_segmentation": partial_segmentation,
                      "statistics": stats, "artifacts": artifacts, "created_at": now()})
    print(f"HTR {job['id']} page {page}: saved in {time.perf_counter() - started:.1f}s", flush=True)
    return page


def summarize(folder, manifest):
    records = [p for p in manifest["expected_pages"]
               if cached(folder / "pages" / f"{p:04d}.json", manifest["fingerprint"])]
    manifest.update(completed_pages=records, complete=len(records) == len(manifest["expected_pages"]), updated_at=now())
    write_json(folder / "run.json", manifest)
    with (folder / "text.txt").open("w", encoding="utf-8") as stream:
        for p in records:
            stream.write(f"\n[HTR UNREVIEWED | {manifest['job']['id']} | file page {p}]\n")
            stream.write((folder / "pages" / f"{p:04d}.txt").read_text(encoding="utf-8"))
            stream.write("\f")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=SCOPES, default="andrew-egor162")
    parser.add_argument("--pages", help="One-based file/manifest positions; omitted means the full historical-text scope")
    parser.add_argument("--jobs", type=int, help="Worker processes (default CPU: 4, CUDA: 1); each uses model memory")
    parser.add_argument("--device", default="cpu", help="cpu or cuda:N (default cpu)")
    parser.add_argument("--batch-size", type=int, default=4, help="Recognition line batch size")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--max-width", type=int, default=4000)
    parser.add_argument("--model", type=Path, help="Default: PP-OCRv6 small on CPU, medium on CUDA")
    parser.add_argument("--segmentation-model", type=Path, default=OUTPUT / "models/blla.mlmodel")
    parser.add_argument("--verify", action="store_true", help="Verify the latest HTR run offline")
    args = parser.parse_args()
    job = next(j for j in json.loads(PLAN.read_text(encoding="utf-8"))["jobs"] if j["id"] == args.only)
    parent = OUTPUT / "htr" / job["id"]
    if args.verify:
        folder = parent / json.loads((parent / "latest.json").read_text(encoding="utf-8"))["run"]
        manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        for source, checksum in manifest["sources"].items():
            if digest(ROOT / source) != checksum:
                raise ValueError("Changed source: " + source)
        for key in ("model", "segmentation_model"):
            model = ROOT / manifest["settings"][key + "_relative"] if key + "_relative" in manifest["settings"] else manifest["settings"][key]
            if digest(model) != manifest["settings"][key + "_sha256"]:
                raise ValueError("Changed model: " + key)
        bad = [p for p in manifest["expected_pages"]
               if not cached(folder / "pages" / f"{p:04d}.json", manifest["fingerprint"])]
        if bad:
            raise ValueError(f"Missing/corrupt HTR pages: {bad}")
        print(f"Verified HTR {job['id']}: {len(manifest['expected_pages'])} pages, UNREVIEWED")
        return 0
    gpu = args.device.startswith("cuda:") and args.device[5:].isdigit()
    if args.device != "cpu" and not gpu:
        parser.error("--device must be cpu or cuda:N")
    args.jobs = args.jobs if args.jobs is not None else (1 if gpu else 4)
    if args.jobs < 1 or args.threads < 1 or args.max_width < 1000 or args.batch_size < 1:
        parser.error("Positive worker/thread counts and --max-width at least 1000 required")
    if args.model is None:
        args.model = OUTPUT / ("models/ppocrv6-medium.safetensors" if gpu else "models/ppocrv6-small.safetensors")
    originals = inputs(job)
    hashes = {p.relative_to(ROOT).as_posix(): verify_original(p) for p in originals}
    count = len(originals)
    if job["kind"] == "pdf":
        import pymupdf
        with pymupdf.open(originals[0]) as doc:
            count = len(doc)
    expected = page_selection(SCOPES[job["id"]], count)
    selected = page_selection(args.pages, count) if args.pages else expected
    if not set(selected) <= set(expected):
        parser.error("Selected pages outside historical-text scope")
    settings = {"engine": "kraken", "algorithm": 2,
                "max_width": args.max_width,
                "segmentation_retry_scales": [1, 0.9, 0.8],
                "threads": args.threads, "kraken": version("kraken"), "torch": version("torch"),
                "pillow": version("pillow"), "dpi": 300, "batch_size": args.batch_size,
                "model": str(args.model.resolve()), "model_sha256": digest(args.model),
                "segmentation_model": str(args.segmentation_model.resolve()),
                "segmentation_model_sha256": digest(args.segmentation_model)}
    if gpu:
        import torch
        index = int(args.device[5:])
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            parser.error(f"CUDA device {index} is unavailable; no CPU fallback was performed")
        settings.update(device=args.device, gpu=torch.cuda.get_device_name(index),
                        cuda=torch.version.cuda, cudnn=torch.backends.cudnn.version())
        for key in ("model", "segmentation_model"):
            model = Path(settings[key])
            if model.is_relative_to(ROOT):
                settings[key + "_relative"] = model.relative_to(ROOT).as_posix()
    if job["kind"] == "pdf":
        settings["pymupdf"] = version("pymupdf")
    identity = {"job": job, "sources": hashes, "scope": expected, "settings": settings}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    folder = parent / fingerprint[:16]
    (folder / "pages").mkdir(parents=True, exist_ok=True)
    lock = folder / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    manifest = {"schema_version": 1, "job": job, "fingerprint": fingerprint, "settings": settings,
                "sources": hashes, "expected_pages": expected, "review_status": "unreviewed",
                "eligible_for_linguistic_analysis": False, "created_at": now(), "errors": []}
    if (folder / "run.json").exists():
        manifest["created_at"] = json.loads((folder / "run.json").read_text(encoding="utf-8"))["created_at"]
    write_json(parent / "latest.json", {"run": folder.name})
    summarize(folder, manifest)
    tasks = [(job, p, str(originals[p - 1] if job["kind"] == "images" else originals[0]),
              settings, str(folder / "pages"), fingerprint)
             for p in selected if not cached(folder / "pages" / f"{p:04d}.json", fingerprint)]
    print(f"HTR {job['id']}: {len(tasks)} pending / {len(expected)} in scope", flush=True)
    try:
        if args.jobs == 1 and tasks:
            initialize(settings)
            for task in tasks:
                try:
                    recognize(task)
                except Exception as e:
                    manifest["errors"].append({"page": task[1], "error": str(e)})
                    print(traceback.format_exc(), file=sys.stderr, flush=True)
            return bool(manifest["errors"])
        context = multiprocessing.get_context("spawn") if gpu else None
        with ProcessPoolExecutor(max_workers=args.jobs, initializer=initialize,
                                 initargs=(settings,), mp_context=context) as pool:
            futures = {pool.submit(recognize, t): t[1] for t in tasks}
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    future.result()
                except Exception as e:
                    manifest["errors"].append({"page": futures[future], "error": str(e)})
                    print(f"ERROR page {futures[future]}: {e}", file=sys.stderr, flush=True)
                    print(traceback.format_exc(), file=sys.stderr, flush=True)
                if i % 5 == 0 or i == len(tasks):
                    print(f"HTR {job['id']}: {i}/{len(tasks)} attempted", flush=True)
    finally:
        try:
            done = summarize(folder, manifest)
            print(f"HTR {job['id']}: {done}/{len(expected)} saved in {folder.relative_to(ROOT)}", flush=True)
        finally:
            lock.unlink(missing_ok=True)
    return bool(manifest["errors"])


if __name__ == "__main__":
    sys.exit(main())
