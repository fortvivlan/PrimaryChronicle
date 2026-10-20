"""Guard source boundaries, script identity and reversible normalization."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("prepare", Path(__file__).resolve().parents[1] / "scripts/prepare_sources.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def extract(self, html, path, source):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / path
            file.parent.mkdir(parents=True)
            encoding = "cp1251" if Path(path).parts[0] in {"lavrlet", "novglet"} else "utf-8"
            file.write_text(html, encoding=encoding, errors="xmlcharrefreplace")
            with patch.object(prepare, "TEXTS", root):
                return prepare.extract_html(Path(path), source)

    def test_bldr_translation_and_notes_are_not_original(self):
        html = '<p class="txt">Introduction</p><h4>* * *</h4><p class="txt">Modern translation</p><h4>* * *</h4><p class="txt">Се лѣто<a><sup>22</sup></a>.</p><div class="note"><p class="txt">Modern note</p></div>'
        result = self.extract(html, "daniel/bldr4-1.html", "daniel")
        self.assertEqual(result[0], "Се лѣто.")
        self.assertNotIn("Modern", result[2])

    def test_chronicle_footnotes_not_superscript_letters_and_1206_cut(self):
        html = '<div class="dop3"><p class="K1">В лЂто<sup>1</sup> бы<sup class="q">с</sup>.</p><div class="dop4"><p class="K1">Editorial prose</p></div><p class="K1"><small><a id="r1206">1206</a></small>Поздно</p><p class="K1">Вне границ</p></div>'
        result = self.extract(html, "lavrlet/lavr23.htm", "suzdal_chronicle")
        self.assertEqual(result[0], "В лѣто быс.")
        self.assertIn("superscript_position_in_evidence_html_only", result[5])
        self.assertNotIn("Editorial", result[2])
        self.assertIn("<sup>с</sup>", result[2])

    def test_normalization_is_source_scoped_and_does_not_guess_pua(self):
        self.assertEqual(prepare.normalize("Ђ\u00a0ѫ\r\nі")[0], "Ђ ѫ\nі")
        text, changes, flags = prepare.normalize("Ђıӕ\ue051", legacy=True)
        self.assertEqual(text, "ѣıӕ\ue051")
        self.assertIn("private_use_encoding", flags)
        self.assertIn("legacy_glyphs_require_expert_mapping", flags)
        self.assertEqual(sum(changes.values()), 1)

    def test_evidence_has_no_active_html(self):
        tree = prepare.Tree('<p onclick="alert(1)">лѣто<script>evil()</script><img onerror="x"><a href="javascript:x">слово</a></p>')
        html = prepare.evidence_html(tree.root.children)
        self.assertEqual(html, "<p>лѣто<span></span><span>слово</span></p>")

    def test_pchela_columns_use_page_margins_and_exclude_wide_notes(self):
        for margin in [80, 350]:
            lines = []
            for y in range(300, 1000, 100):
                lines.extend([{"text": "Се слово древнее", "box": [margin, y, margin+350, y+40]},
                              {"text": "λογος και πατηρ", "box": [margin+400, y, margin+750, y+40]}])
            lines.append({"text": "Примечание издателя", "box": [margin, 1200, margin+750, 1230]})
            selected, status = prepare.pchela_body(lines, 1300, 1700)
            self.assertEqual(len(selected), 7)
            self.assertTrue(all(l["text"] == "Се слово древнее" for l in selected))

    def test_unrecognized_bldr_layout_fails_instead_of_extracting_translation(self):
        with self.assertRaisesRegex(ValueError, "layout changed"):
            self.extract('<h4>* * *</h4><p class="txt">translation</p>', "daniel/bldr4-1.html", "daniel")


if __name__ == "__main__":
    unittest.main()
