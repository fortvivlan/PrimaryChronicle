#!/usr/bin/env python3
"""Export the largest DOCX table using only the Python standard library."""

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, is_zipfile

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
ROOT = Path(__file__).resolve().parents[1]


def run_text(run):
    result = []
    for node in run:
        if node.tag == W + "t":
            result.append(node.text or "")
        elif node.tag in (W + "br", W + "cr"):
            result.append("\n")
        elif node.tag == W + "tab":
            result.append("\t")
        elif node.tag == W + "sym":
            # Symbol-font codepoints used in this document, mapped to Unicode:
            # https://www.unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt
            symbols = {("Symbol", "F0C6"): "∅", ("Symbol", "F0AE"): "→"}
            key = (node.get(W + "font"), node.get(W + "char"))
            if key not in symbols:
                raise ValueError(f"Unmapped Word symbol: {key}")
            result.append(symbols[key])
    return "".join(result)


def cell_content(cell):
    paragraphs, formatted = [], []
    for paragraph in cell.findall("w:p", NS):
        plain, rich = [], []
        for run in paragraph.findall(".//w:r", NS):
            value = run_text(run)
            plain.append(value)
            value = html.escape(value)
            properties = run.find("w:rPr", NS)
            if properties is not None:
                for prop, tag in (("b", "strong"), ("i", "em"), ("strike", "s")):
                    element = properties.find("w:" + prop, NS)
                    if element is not None and element.get(W + "val") not in ("0", "false", "off"):
                        value = f"<{tag}>{value}</{tag}>"
                vertical = properties.find("w:vertAlign", NS)
                if vertical is not None:
                    tag = {"superscript": "sup", "subscript": "sub"}.get(vertical.get(W + "val"))
                    if tag:
                        value = f"<{tag}>{value}</{tag}>"
            rich.append(value)
        paragraphs.append("".join(plain))
        formatted.append("".join(rich))
    return "\n".join(paragraphs).strip(), "\n".join(formatted).strip()


def subjects(syntax):
    match = re.search(r"подлежащее\s*:\s*([^\n]+)", syntax, re.I)
    raw = match.group(1).strip() if match else ""
    labels = re.findall(r"\b(?:pro|NP|QP|AP|wh)\b", raw)
    by_edition = {"lavr": labels, "ipat": labels}
    # Explicit edition qualifications in two source rows; preserve the raw label.
    if re.search(r"pro\s+Л(?:ХП)?\s*/\s*NP\s+И", raw):
        by_edition = {"lavr": ["pro"], "ipat": ["NP"]}
    return raw, by_edition


def normalize_protograph(value):
    # Collapse typographical variants, without conflating uncertain readings.
    return re.sub(r"\s*([~/])\s*", r" \1 ", " ".join(value.split()).replace("Iа", "Ia"))


def extract(source):
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    table = max(root.findall(".//w:tbl", NS), key=lambda t: len(t.findall("w:tr", NS)))
    rows = table.findall("w:tr", NS)
    records, inherited = [], {}
    previous_year = ("", "")
    for index, row in enumerate(rows[2:], 1):
        cells = row.findall("w:tc", NS)
        if len(cells) != 9:
            raise ValueError(f"Unexpected column count in source row {index + 2}")
        values = []
        for column, cell in enumerate(cells):
            value = cell_content(cell)
            if column == 0:
                source_year = value[0]
            merge = cell.find("w:tcPr/w:vMerge", NS)
            if merge is not None and merge.get(W + "val") != "restart":
                if column not in inherited:
                    raise ValueError(f"Missing merged-cell origin in row {index + 2}")
                value = inherited[column]
            elif merge is not None:
                inherited[column] = value
            else:
                inherited.pop(column, None)
            values.append(value)
        # The source also uses unmerged blank year cells as continuation rows.
        if values[0][0]:
            previous_year = values[0]
        else:
            values[0] = previous_year
        text = [v[0] for v in values]
        year_match = re.search(r"\b\d{3,4}\b", text[0])
        subject_raw, subject_by_edition = subjects(text[6])
        editions = {}
        for key, main, variants in (("lavr", 2, 3), ("ipat", 4, 5)):
            editions[key] = {
                "text": text[main], "html": values[main][1],
                "variants": text[variants], "variantsHtml": values[variants][1],
                "available": bool(text[main] or text[variants]),
            }
        records.append({
            "id": index, "sourceRow": index + 2,
            "year": int(year_match.group()) if year_match else None,
            "yearLabel": text[0], "sourceYear": source_year,
            "folio": text[1], "editions": editions,
            "syntax": text[6], "semantics": text[7],
            "subjectRaw": subject_raw, "subjects": subject_by_edition,
            "protograph": text[8], "protographKey": normalize_protograph(text[8]),
        })
    return {
        "metadata": {
            "source": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "tableIndex": list(root.findall(".//w:tbl", NS)).index(table),
            "headerRows": 2, "total": len(records),
            "notes": "One record per source table row. Merged cells expanded; blank year continuations inherit the previous year; no semantic labels inferred.",
        },
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/records.json")
    args = parser.parse_args()
    candidates = sorted(p for p in (ROOT / "data").glob("*.docx") if not p.name.startswith("~$") and is_zipfile(p))
    source = args.source or (candidates[0] if len(candidates) == 1 else None)
    if source is None:
        parser.error("Specify a DOCX source; expected exactly one document in data/.")
    result = extract(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {result['metadata']['total']} records to {args.output}")


if __name__ == "__main__":
    main()
