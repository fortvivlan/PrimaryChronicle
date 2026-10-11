import json
import unittest
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.extract_data import NS, W, cell_content, extract, normalize_protograph

ROOT = Path(__file__).resolve().parents[1]


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag not in ("strong", "em", "s", "sup", "sub") or attrs:
            raise AssertionError(f"Unexpected generated markup: {tag}, {attrs}")


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.dataset = json.loads((ROOT / "docs/assets/records.json").read_text())
        self.records = self.dataset["records"]

    def test_row_count_dates_and_source_rows(self):
        self.assertEqual(len(self.records), 179)
        self.assertEqual([r["sourceRow"] for r in self.records], list(range(3, 182)))
        self.assertTrue(all(r["year"] is None for r in self.records[:8]))
        self.assertEqual(self.records[8]["year"], 852)
        self.assertEqual(self.records[10]["year"], 898)
        self.assertEqual(self.records[11]["year"], 898)
        self.assertEqual(self.records[22]["year"], 944)
        self.assertEqual(self.records[22]["sourceYear"], "")
        self.assertEqual(self.records[150]["year"], 1097)
        self.assertEqual(self.records[-1]["year"], 1115)
        self.assertEqual(sum(r["year"] is None for r in self.records), 8)

    def test_symbol_font_and_escaping(self):
        cell = ET.fromstring(f'''<w:tc xmlns:w="{NS['w']}"><w:p>
          <w:r><w:t>&lt;text&gt; &amp; </w:t><w:sym w:font="Symbol" w:char="F0C6"/></w:r>
          <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>1</w:t></w:r>
          <w:r><w:sym w:font="Symbol" w:char="F0AE"/></w:r>
          </w:p></w:tc>''')
        plain, rich = cell_content(cell)
        self.assertEqual(plain, "<text> & ∅1→")
        self.assertEqual(rich, "&lt;text&gt; &amp; ∅<sup>1</sup>→")
        self.assertIn("∅", self.records[2]["syntax"])
        self.assertEqual(self.records[33]["protograph"], "→ Плюсквамперфект")

    def test_rich_text_preserves_all_exported_passages(self):
        for record in self.records:
            for edition in record["editions"].values():
                for plain, rich in (("text", "html"), ("variants", "variantsHtml")):
                    parser = TextParser()
                    parser.feed(edition[rich])
                    self.assertEqual("".join(parser.parts).strip(), edition[plain])

    def test_missing_annotations_are_not_inferred(self):
        self.assertEqual(sum(r["semantics"] == "" for r in self.records), 120)
        self.assertEqual(sum(r["subjectRaw"] == "" for r in self.records), 3)
        self.assertEqual(sum(r["protograph"] == "" for r in self.records), 3)
        self.assertEqual(self.records[64]["subjects"], {"lavr": ["pro"], "ipat": ["NP"]})

    def test_normalization_keeps_reconstruction_distinctions(self):
        self.assertEqual(normalize_protograph("Перфект  Iа  ~  Аорист"), "Перфект Ia ~ Аорист")
        self.assertNotEqual(normalize_protograph("→ Аорист"), normalize_protograph("Аорист"))

    def test_export_matches_source_when_available(self):
        source = ROOT / "data" / self.dataset["metadata"]["source"]
        if not source.exists():
            self.skipTest("Ignored source DOCX is not present in this checkout")
        self.assertEqual(extract(source), self.dataset)


if __name__ == "__main__":
    unittest.main()
