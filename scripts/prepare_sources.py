#!/usr/bin/env python3
"""Build a traceable Old Russian collection and a portable, serverless review kit.

Only the maintainer runs this script. The reviewer opens data/expert_review/index.html.
Original downloads, OCR runs, and manual PVL annotations are never modified.
"""

import argparse
from collections import Counter
import csv
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEXTS = ROOT / "data/texts"
OCR = ROOT / "data/ocr"
OUT = ROOT / "data/old_russian"
KIT = ROOT / "data/expert_review"
ASSETS = ROOT / "review"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def has(self, cls):
        return cls in self.attrs.get("class", "").split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def ancestors(self):
        p = self.parent
        while p:
            yield p
            p = p.parent


class Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        # Source pages contain optional closing paragraph tags.
        if tag == "p":
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == "p":
                    self.stack = self.stack[:i]
                    break
        n = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def flatten(node, mode="all"):
    if isinstance(node, str):
        return node
    if node.tag in {"script", "style", "noscript", "form"}:
        return ""
    if mode == "main":
        if node.tag == "sup" and not node.has("q"):
            return ""
        if node.tag in {"small", "strong"} and (
            re.match(r"(?:lyst|page)", node.attrs.get("id", ""))
            or any(re.match(r"[rl]\d+", n.attrs.get("id", "")) for n in node.walk())
        ):
            return ""
    if node.tag == "br":
        return "\n"
    return "".join(flatten(c, mode) for c in node.children)


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def evidence_html(nodes):
    """Allow text formatting only; never carry source scripts, links or event handlers."""
    def render(n):
        if isinstance(n, str):
            return escape(n)
        if n.tag in {"script", "style", "noscript", "form"}:
            return ""
        content = "".join(render(c) for c in n.children)
        tag = n.tag if n.tag in {"p", "b", "strong", "i", "em", "sup", "sub", "small", "br"} else "span"
        return "<br>" if tag == "br" else f"<{tag}>{content}</{tag}>"
    return "\n".join(render(n) for n in nodes)


def normalize(text, legacy=False):
    # NFC only: NFKC would erase distinctions in historic letters and diacritics.
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " "))
    changes = {}
    if legacy and "Ђ" in text:
        changes["U+0402 → U+0463 (Изборник: ять)"] = text.count("Ђ")
        text = text.replace("Ђ", "ѣ")
    flags = []
    if any(unicodedata.category(c) == "Co" for c in text):
        flags.append("private_use_encoding")
    if legacy and any(c in text for c in "ıӕӔү"):
        flags.append("legacy_glyphs_require_expert_mapping")
    if re.search(r"[A-Za-z]", text):
        flags.append("latin_characters_check_against_source")
    if re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text):
        flags.append("greek_characters_check_against_source")
    return text, changes, flags


def extract_html(path, source_id):
    """Explicit source selectors, not a language guess applied to whole web pages."""
    encoding = "cp1251" if path.parts[0] in {"lavrlet", "ipatlet", "novglet", "monomakh", "antony"} else "utf-8"
    html = (TEXTS / path).read_text(encoding=encoding)
    root = Tree(html).root
    all_nodes = list(root.walk())
    paragraphs = [n for n in all_nodes if n.tag == "p"]
    legacy = path.parts[0] in {"lavrlet", "ipatlet", "novglet", "monomakh"}
    selected = []
    selector = ""
    if legacy:
        selector = "p.K1 / p.KF; exclude apparatus and navigation; retain superscript letters"
        for p in paragraphs:
            if not (p.has("K1") or p.has("KF")):
                continue
            if any(a.has(c) for a in p.ancestors() for c in ["dop4", "dop5", "pidnyz", "nyz", "gora"]):
                continue
            if source_id == "suzdal_chronicle" and any(n.attrs.get("id") == "r1206" for n in p.walk()):
                break
            if source_id == "novgorod_first_older" and any(n.attrs.get("id") == "r1205" for n in p.walk()):
                break
            t = clean(flatten(p, "main"))
            if t and re.search(r"[А-Яа-яѠ-ѿ]", t):
                selected.append(p)
    elif path.name.startswith("bldr"):
        # The saved BLDR pages have two *** headings: translation, then original.
        heads = [n for n in all_nodes if n.tag == "h4" and clean(flatten(n)) == "* * *"]
        if len(heads) != 2:
            raise ValueError(f"BLDR section layout changed: {path}")
        start = all_nodes.index(heads[1])
        selector = "p.txt after second h4(***) and before notes; exclude translation and introduction"
        for p in all_nodes[start + 1:]:
            if p.tag == "div" and p.has("note"):
                break
            if p.tag == "p" and p.has("txt"):
                selected.append(p)
    elif source_id == "antony":
        selector = "paragraphs in #main before #dann; note anchors removed"
        for p in paragraphs:
            if any(a.attrs.get("id") == "dann" for a in p.ancestors()):
                break
            if any(a.attrs.get("id") == "main" for a in p.ancestors()):
                selected.append(p)
    elif source_id in {"ilya_ioann", "kirik"}:
        selector = "explicit incipit → first *** apparatus separator"
        active = False
        incipit = "Се Богови, братие" if source_id == "ilya_ioann" else "Се Есть вопрашание Кириково"
        for p in paragraphs:
            t = clean(flatten(p))
            if t.startswith(incipit):
                active = True
            if active and t == "* * *":
                break
            if active:
                selected.append(p)
    elif path.name == "manuscript-excerpt.html":
        selector = "positioned manuscript columns only; PUA retained, conversion blocked"
        # Retain line boundaries and angle-bracket editorial restorations.
        columns = [n for n in all_nodes if n.tag == "span" and re.search(r"top:\s*30px", n.attrs.get("style", ""))]
        def no_counters(n):
            if isinstance(n, str):
                return n
            if n.has("cnt"):
                return ""
            if n.tag == "br":
                return "\n"
            return "".join(no_counters(c) for c in n.children)
        raw = "\n\n".join("\n".join(l.strip() for l in no_counters(n).splitlines() if l.strip()) for n in columns)
        if not raw:
            raise ValueError(f"No manuscript columns: {path}")
        text, changes, flags = normalize(raw)
        return text, raw, evidence_html(columns), selector, changes, flags, []
    if not selected:
        raise ValueError(f"No original text selected: {path}")
    raw = "\n\n".join(clean(flatten(p, "main")) for p in selected if clean(flatten(p, "main")))
    if source_id == "ilya_ioann":
        # This web transcription flattened its numeric footnote markers into body text.
        raw = re.sub(r"(?<=[А-Яа-яѠ-ѿ҃҄҅])\d{1,3}", "", raw)
    text, changes, flags = normalize(raw, legacy)
    if any(n.tag == "sup" and n.has("q") for p in selected for n in p.walk()):
        flags.append("superscript_position_in_evidence_html_only")
    locators = [n.attrs["id"] for p in selected for n in p.walk() if "id" in n.attrs]
    return text, raw, evidence_html(selected), selector, changes, flags, locators


def scripts(text):
    cyr = sum("CYRILLIC" in unicodedata.name(c, "") for c in text)
    greek = sum("GREEK" in unicodedata.name(c, "") for c in text)
    return cyr, greek


def recognition_lines(folder, page):
    stem = folder / "pages" / f"{page:04d}"
    line_path = stem.with_suffix(".lines.json")
    if line_path.exists():
        lines = []
        for l in read_json(line_path)["lines"]:
            points = l.get("boundary") or l.get("baseline") or []
            if not points:
                continue
            xs, ys = zip(*points)
            lines.append({"text": l["text"], "box": [min(xs), min(ys), max(xs), max(ys)]})
        return lines
    groups = {}
    with stem.with_suffix(".tsv").open(encoding="utf-8", newline="") as stream:
        for word in csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE):
            if word.get("level") != "5" or not word.get("text", "").strip():
                continue
            key = tuple(word[k] for k in ["block_num", "par_num", "line_num"])
            x, y, w, h = (int(word[k]) for k in ["left", "top", "width", "height"])
            groups.setdefault(key, []).append((word["text"], [x, y, x + w, y + h]))
    return [{"text": " ".join(w[0] for w in words), "box": [min(w[1][0] for w in words), min(w[1][1] for w in words), max(w[1][2] for w in words), max(w[1][3] for w in words)]} for words in groups.values()]


def pchela_body(lines, width, height):
    """Provisional column segmentation; every excluded line remains in evidence."""
    # Binding margins alternate: infer column centers for each page, not a fixed x.
    eligible = [l for l in lines if l["box"][2] - l["box"][0] < .46 * width
                and .12 * height < l["box"][1] < .85 * height and sum(scripts(l["text"])) >= 8]
    centers = [(l["box"][0] + l["box"][2]) / 2 for l in eligible]
    if len(centers) < 4:
        return [], "column_detection_failed"
    left, right = min(centers), max(centers)
    for _ in range(12):
        a = [x for x in centers if abs(x-left) <= abs(x-right)]
        b = [x for x in centers if abs(x-left) > abs(x-right)]
        if not a or not b:
            return [], "column_detection_failed"
        left, right = sum(a)/len(a), sum(b)/len(b)
    if right - left < .15 * width:
        return [], "column_detection_failed"
    split = (left + right) / 2
    wide = [l["box"][1] for l in lines if l["box"][2]-l["box"][0] > .52*width and l["box"][1] > .5*height]
    footer = min(wide, default=.84 * height)
    selected = []
    for l in lines:
        x1, y1, x2, y2 = l["box"]
        cyr, greek = scripts(l["text"])
        if (.12*height <= y1 < footer and x2-x1 < .46*width and (x1+x2)/2 < split
                and cyr >= 2 and greek <= max(1, cyr*.1)):
            selected.append(l)
    return selected, "automatic_left_column_and_footer_detection_requires_review"


def compress_preview(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        # Existing recognition setup may keep Pillow here, outside system Python.
        sys.path.insert(0, str(OCR / "tools/python"))
        try:
            from PIL import Image
        except ImportError:
            shutil.copyfile(source, target)
            return "original_preview_copy"
    with Image.open(source) as im:
        im.thumbnail((1600, 1900))
        im.convert("RGB").save(target, "JPEG", quality=76, optimize=True)
    return "resized_preview_not_coordinate_raster"


def build(offline_images=False, make_zip=False):
    catalog = read_json(ROOT / "sources/catalog.json")
    plan = read_json(ROOT / "sources/ocr-plan.json")
    sources = {s["id"]: s for s in catalog["sources"]}
    records, inventory, audit = [], [], []
    # Reuse only previews from the same recognition fingerprint, and only when
    # their bytes still match the previous package's checksums.
    previous_manifest = read_json(OUT / "manifest.json") if (OUT / "manifest.json").exists() else {}
    previous_records = {r["id"]: r for r in previous_manifest.get("records", [])}
    previous_checksums = read_json(KIT / "checksums.json") if (KIT / "checksums.json").exists() else {}
    KIT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    remote = {}
    for name in ["andrew", "pchela"]:
        for f in read_json(ROOT / f"sources/{name}-pages.json"):
            remote["data/texts/" + f["path"]] = f["url"]

    def add(record, directory):
        text = record["text"]
        record.update({"review_status": "unreviewed", "eligible_for_linguistic_analysis": False,
                       "text_sha256": digest(text), "text_path": f"{directory}/{record['id']}.txt"})
        dest = OUT / record["text_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text + "\n", encoding="utf-8")
        record["file_sha256"] = sha(dest)
        records.append(record)

    for s in catalog["sources"]:
        if s["id"] == "support":
            continue
        audit.append({"id": s["id"], "title": s["title"], "edition": s["edition"],
                      "catalog_coverage": s["coverage"], "notes": s["notes"], "actions": []})
        for f in s["files"]:
            file = TEXTS / f["path"]
            disposition = "context_only"
            if f.get("role") == "modern_translation":
                disposition = "excluded_modern_translation"
            elif f.get("role") == "text" and f["kind"] == "html":
                path = Path(f["path"])
                text, raw, evidence, selector, changes, flags, locators = extract_html(path, s["id"])
                blocked = "private_use_encoding" in flags
                disposition = "encoding_blocked" if blocked else "original_text_candidate"
                rid = s["id"] + "--" + path.stem
                add({"id": rid, "source_id": s["id"], "label": path.name, "kind": "html",
                     "method": "automatic_html_extraction", "text": text, "source_text": raw,
                     "evidence_html": evidence, "source_url": f["url"],
                     "source_path": "data/texts/" + f["path"], "source_sha256": sha(file),
                     "selector": selector, "normalization_changes": changes, "flags": flags,
                     "locators": locators, "segmentation": disposition,
                     "legacy_font": "menaion" if blocked else "timeslit" if path.parts[0] in {"lavrlet", "ipatlet", "monomakh"} else None},
                    "encoding_blocked" if blocked else "texts")
            elif f["kind"] in {"pdf", "zip"}:
                disposition = "scan_recognition_review"
            inventory.append({"source_id": s["id"], "path": "data/texts/" + f["path"],
                              "role": f.get("role"), "disposition": disposition,
                              "exists": file.exists(), "bytes": file.stat().st_size if file.exists() else 0})

    # Read live latest pointers; never silently substitute an older complete run.
    jobs = {j["id"]: j for j in plan["jobs"]}
    runs = []
    pointers = sorted(OCR.glob("*/latest.json")) + sorted((OCR / "htr").glob("*/latest.json"))
    for pointer in pointers:
        folder = pointer.parent / read_json(pointer)["run"]
        manifest = read_json(folder / "run.json")
        job = jobs[manifest["job"]["id"]]
        engine = manifest["settings"].get("engine", "tesseract")
        # Prefer HTR for historical script; retain the other reading as an alternative.
        specialized = OCR / "htr" / job["id"] / "latest.json"
        if engine == "tesseract" and specialized.exists():
            htr = specialized.parent / read_json(specialized)["run"]
        else:
            htr = None
        chosen = []
        missing = []
        for page in manifest["expected_pages"]:
            scope = next((s["role"] for s in job.get("sections", []) if s["first"] <= page <= s["last"]), "edition_context")
            if job["id"] == "andrew-egor162":
                scope = "manuscript_transcription_candidate"
            if scope == "edition_context" and job["id"] != "kirik-rib6":
                continue
            if htr and page in read_json(htr / "run.json")["expected_pages"]:
                # That primary record will be produced by the HTR pointer below.
                continue
            metadata = folder / "pages" / f"{page:04d}.json"
            if not metadata.exists():
                missing.append(page)
                continue
            meta = read_json(metadata)
            stem = folder / "pages" / f"{page:04d}"
            if meta.get("fingerprint") != manifest["fingerprint"]:
                raise ValueError(f"Recognition fingerprint mismatch: {metadata}")
            original = stem.with_suffix(".txt")
            expected_hash = meta["artifacts"].get(original.name)
            if sha(original) != expected_hash:
                raise ValueError(f"Recognition text checksum mismatch: {original}")
            raw = original.read_text(encoding="utf-8")
            lines = recognition_lines(folder, page)
            width, height = meta["ocr_pixel_size"]
            flags = ["recognition_requires_proofreading", "base_text_variants_and_boundaries_require_review"]
            if meta.get("partial_segmentation"):
                flags.append("partial_segmentation")
            segmentation = "mixed_page_requires_selection"
            selected = []
            if job["id"] == "pchela-1893":
                selected, flag = pchela_body(lines, width, height)
                flags.append(flag)
                segmentation = "provisional_original_column" if selected else "mixed_page_requires_selection"
            elif job["id"] == "andrew-egor162":
                selected = lines
                segmentation = "manuscript_boundaries_require_review"
            # Mixed PDF / DjVu pages stay outside the Old Russian text candidates.
            # The reviewer must select the original body, not rubber-stamp a page.
            body = "\n".join(l["text"] for l in selected) if selected else raw
            text, changes, encoding_flags = normalize(body)
            flags.extend(encoding_flags)
            rid = f"{job['id']}--{page:04d}"
            remote_url = remote.get(meta["source"])
            local_image = None
            if offline_images or not remote_url:
                local_image = f"images/{rid}.jpg"
                target = KIT / local_image
                cached = previous_records.get(rid, {}).get("run_fingerprint") == manifest["fingerprint"]
                if not (cached and target.exists() and sha(target) == previous_checksums.get(local_image)):
                    compress_preview(stem.with_suffix(".preview.jpg"), target)
            source_file = meta["source"].replace("\\", "/")
            # Original URLs, not invented thumbnail or CORS proxy endpoints.
            source_url = remote_url
            if not source_url:
                source_url = next((f["url"] for f in sources[job["source_id"]]["files"]
                                   if source_file.endswith(f["path"])), None)
            alternatives = []
            if engine != "tesseract":
                tp = OCR / job["id"] / "latest.json"
                if tp.exists():
                    tf = tp.parent / read_json(tp)["run"] / "pages" / f"{page:04d}.txt"
                    if tf.exists():
                        alternatives.append({"label": "Tesseract (вся страница)", "text": tf.read_text(encoding="utf-8"), "path": tf.relative_to(ROOT).as_posix()})
            add({"id": rid, "source_id": job["source_id"], "label": f"{job['id']} · файл / позиция {page}",
                 "kind": "recognition", "method": meta["method"], "text": text, "source_text": raw,
                 "source_path": source_file, "source_url": source_url,
                 "recognition_path": original.relative_to(ROOT).as_posix(), "recognition_sha256": expected_hash,
                 "run_fingerprint": manifest["fingerprint"], "page": page,
                 "page_numbering": "one_based_file_or_manifest_position_not_printed_page_or_folio",
                 "scope": scope, "segmentation": segmentation, "flags": flags,
                 "statistics": meta["statistics"], "normalization_changes": changes,
                 "remote_image": remote_url, "local_image": local_image,
                 "image_size": meta["ocr_pixel_size"], "lines": lines,
                 "selected_boxes": [l["box"] for l in selected], "alternatives": alternatives},
                "ocr_candidates" if selected else "needs_segmentation")
            chosen.append(page)
        runs.append({"job": job["id"], "engine": engine, "fingerprint": manifest["fingerprint"],
                     "expected_pages": len(manifest["expected_pages"]), "packaged_pages": chosen,
                     "missing_relevant_pages": missing, "errors": manifest.get("errors", [])})

    for item in audit:
        subset = [r for r in records if r["source_id"] == item["id"]]
        item["records"] = len(subset)
        item["segmentation_counts"] = dict(Counter(r["segmentation"] for r in subset))
        item["flags"] = dict(Counter(f for r in subset for f in r["flags"]))
        if item["id"] == "kirik":
            item["actions"].append("РИБ 6: 682 страницы — вспомогательная очередь. Выделить границы памятников; HTML содержит также другие канонические тексты, пока не извлечённые отдельно.")
        if item["id"] in {"andrew_fool", "nicholas_miracles", "pchela"}:
            item["actions"].append("Не смешивать разные списки и редакции; БЛДР / Манускрипт могут быть только выборкой, не заменяют полную рукопись.")
        if item["id"] in {"pchela", "nicholas_miracles"}:
            item["actions"].append("Манускрипт: PUA сохранены с Menaion; нужна подтверждённая таблица конверсии. Эти файлы находятся в encoding_blocked/.")
        if item["id"] in {"kirik", "josephus", "pchela", "andrew_fool"}:
            item["actions"].append("Вычитать OCR/HTR по изображениям, выделить основной текст, исключить аппарат и посторонние разделы; проверить пропущенные строки.")

    # A file-level manifest accounts for assets beyond the catalog's text entries.
    known = {i["path"] for i in inventory}
    for file in sorted(TEXTS.rglob("*")):
        rel = file.relative_to(ROOT).as_posix()
        if not file.is_file() or file.name.endswith(".receipt.json") or rel in known:
            continue
        inventory.append({"path": rel, "bytes": file.stat().st_size,
                          "disposition": "scan_image" if rel in remote else "support_or_context",
                          "exists": True})
    summary = {"schema_version": 1, "policy": "All automatic extraction remains unreviewed. Review and segmentation are separate from linguistic annotation.",
               "normalization": "UTF-8, LF, NFC; NBSP→space; source-scoped Ђ→ѣ only. No NFKC, spelling modernization or guessed PUA conversion.",
               "record_count": len(records), "inventory": inventory, "sources": audit, "runs": runs,
               "directories": {"texts": "Original HTML text candidates", "ocr_candidates": "Provisional OCR/HTR original-text candidates",
                               "needs_segmentation": "Mixed OCR evidence: NOT isolated Old Russian", "encoding_blocked": "PUA originals: NOT standardized Unicode"}}
    manifest_records = [{k: v for k, v in r.items() if k not in {"text", "source_text", "evidence_html", "lines", "selected_boxes", "alternatives"}} for r in records]
    write_json(OUT / "manifest.json", {**summary, "records": manifest_records})
    write_json(ROOT / "sources/preparation-report.json", summary)
    for name in ["index.html", "app.js", "style.css", "review-core.js"]:
        shutil.copyfile(ASSETS / name, KIT / name)
    (KIT / "fonts").mkdir(exist_ok=True)
    for src, dest in [("shryft/timeslit.ttf", "timeslit.ttf"), ("fonts/menaion.ttf", "menaion.ttf")]:
        shutil.copyfile(TEXTS / src, KIT / "fonts" / dest)
    package_id = digest("\n".join(r["id"] + ":" + r["text_sha256"] + ":" +
                                 r.get("source_sha256", r.get("run_fingerprint", ""))
                                 for r in records))
    bundle = {"schema_version": 1, "package_id": package_id, "sources": audit, "records": records,
              "normalization": summary["normalization"]}
    # A classic script works on file://; fetch(JSON), modules and a server are unnecessary.
    (KIT / "data.js").write_text("window.REVIEW_DATA = " + json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n", encoding="utf-8")
    shutil.copyfile(ROOT / "sources/REVIEW.md", KIT / "README.md")
    write_json(KIT / "audit.json", summary)
    files = sorted(p for p in KIT.rglob("*") if p.is_file() and p.name != "checksums.json")
    write_json(KIT / "checksums.json", {p.relative_to(KIT).as_posix(): sha(p) for p in files})
    size = sum(p.stat().st_size for p in KIT.rglob("*") if p.is_file())
    if make_zip:
        with zipfile.ZipFile(ROOT / "data/expert_review.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for p in KIT.rglob("*"):
                if p.is_file():
                    archive.write(p, "expert_review/" + p.relative_to(KIT).as_posix())
    print(f"Prepared {len(records)} records; expert kit {size / 1024**2:.1f} MiB; open {KIT / 'index.html'}")
    print(dict(Counter(r["segmentation"] for r in records)))
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-images", action="store_true", help="Include previews for remote manuscript / Pchela images too")
    parser.add_argument("--zip", action="store_true", help="Also create data/expert_review.zip for handoff")
    args = parser.parse_args()
    build(args.offline_images, args.zip)
