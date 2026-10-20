"""Integrity and page-identity regressions; no OCR installation required."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

SPEC = importlib.util.spec_from_file_location("ocr", Path(__file__).resolve().parents[1] / "scripts/ocr_sources.py")
ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr)
HTR_SPEC = importlib.util.spec_from_file_location("htr", Path(__file__).resolve().parents[1] / "scripts/htr_sources.py")
htr = importlib.util.module_from_spec(HTR_SPEC)
with patch.dict(sys.modules, {"ocr_sources": ocr}):
    HTR_SPEC.loader.exec_module(htr)


class IntegrityTests(unittest.TestCase):
    def test_htr_resume_requires_all_intact_line_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            artifacts = {}
            for suffix in (".txt", ".alto.xml", ".lines.json", ".preview.jpg"):
                artifact = folder / ("0002" + suffix)
                artifact.write_bytes(b"recognition evidence")
                artifacts[artifact.name] = ocr.digest(artifact)
            record = folder / "0002.json"
            record.write_text(json.dumps({"fingerprint": "gpu-run", "artifacts": artifacts}))
            self.assertTrue(htr.cached(record, "gpu-run"))
            self.assertFalse(htr.cached(record, "cpu-run"))
            (folder / "0002.lines.json").write_bytes(b"interrupted write")
            self.assertFalse(htr.cached(record, "gpu-run"))
            record.write_text(json.dumps({"fingerprint": "gpu-run", "artifacts": {}}))
            self.assertFalse(htr.cached(record, "gpu-run"))

    def test_truncated_or_changed_outputs_are_not_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            artifacts = {}
            for suffix in (".txt", ".tsv", ".hocr", ".embedded.txt", ".log", ".preview.jpg"):
                p = folder / ("0001" + suffix)
                p.write_bytes(b"original output")
                artifacts[p.name] = ocr.digest(p)
            record = folder / "0001.json"
            record.write_text(json.dumps({"fingerprint": "run-a", "artifacts": artifacts}))
            self.assertTrue(ocr.cached(record, "run-a"))
            self.assertFalse(ocr.cached(record, "run-b"))
            (folder / "0001.txt").write_bytes(b"truncated")
            self.assertFalse(ocr.cached(record, "run-a"))
            record.write_text(json.dumps({"fingerprint": "run-a", "artifacts": {}}))
            self.assertFalse(ocr.cached(record, "run-a"))

    def test_source_receipt_failure_does_not_modify_original(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.pdf"
            source.write_bytes(b"source evidence")
            receipt = source.with_name("source.pdf.receipt.json")
            receipt.write_text(json.dumps({"sha256": ocr.digest(source), "bytes": source.stat().st_size}))
            self.assertEqual(ocr.verify_original(source), ocr.digest(source))
            source.write_bytes(b"altered source")
            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "Original differs"):
                ocr.verify_original(source)
            self.assertEqual(source.read_bytes(), before)

    def test_physical_pages_are_one_based_and_cannot_escape_document(self):
        self.assertEqual(ocr.page_selection("3-5,1,4", 5), [1, 3, 4, 5])
        for spec in ("0", "6", "4-2", "1-2-3"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                ocr.page_selection(spec, 5)

    def test_tsv_tabs_and_empty_pages_do_not_invent_words(self):
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + "5\t1\t1\t1\t1\t1\t10\t10\t20\t20\t30\tлѣто\n"
        stats = ocr.word_stats(tsv)
        self.assertEqual(stats["words"], 1)
        self.assertEqual(stats["historical_cyrillic_characters"], 1)
        self.assertEqual(stats["low_confidence_words"], 1)
        self.assertIsNone(ocr.word_stats(header)["mean_word_confidence"])


if __name__ == "__main__":
    unittest.main()
