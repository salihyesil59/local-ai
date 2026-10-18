import shutil
import zipfile

import pytest

from local_ai.research import document as doc
from local_ai.servers.export import ExportError, export_document

SOURCES = [
    {"n": 1, "title": "Speed of light", "origin": "wikipedia", "locator": "intro"},
    {"n": 2, "title": "Quantum gravity", "origin": "arxiv", "locator": "", "authors": "Doe, Jane", "year": "2024",
     "doi": "10.1/x"},
]
CHAPTERS = ["## 1. Işık hızı nedir?\n\nc = 299792458 m/s [1].\n\n### Ölçüm\n\nAyrıntı.", "## 2. Kütleçekim\n\nB [2]."]
SYNTHESIS = "# Cevap\n\nIşık hızlıdır [1].\n\n## Sınırlar\n\nBilinmeyenler.\n\n## Kaynaklar\n\n[1] eski liste"


def test_headings_move_but_code_stays():
    text = "# A\n\n## B\n\n```python\n# a comment\n```\n#not a heading"
    assert doc.shift_headings(text) == "## A\n\n### B\n\n```python\n# a comment\n```\n#not a heading"
    assert doc.shift_headings("plain text") == "plain text"


def test_the_document_has_the_chapters_then_the_synthesis_then_the_sources():
    text = doc.build('Işık "hızı"?', "tr", CHAPTERS, SYNTHESIS, SOURCES)
    assert text.startswith('---\ntitle: "Işık \\"hızı\\"?"\n') and "lang: tr-TR" in text
    order = ["# Bölüm I — Araştırma bulguları", "## 1. Işık hızı nedir?", "### Ölçüm", "## 2. Kütleçekim",
             "# Bölüm II — Sentez", "## Cevap", "### Sınırlar", "w:br", "\\newpage", "# Kaynakça"]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "eski liste" not in text  # the model's own reference list goes; the registered sources come instead
    assert "[1] *Speed of light* (wikipedia — intro)" in text
    assert "[2] Doe, Jane (2024). *Quantum gravity* (arxiv) doi:10.1/x" in text


def test_english_and_no_chapters_or_sources():
    text = doc.build("Why?", "en", [], "Because.", [])
    assert "# Part II — Synthesis" in text and "Part I —" not in text and "References" not in text


def test_a_document_needs_its_file_and_a_known_format(tmp_path):
    with pytest.raises(ExportError, match="docx or pdf"):
        export_document(tmp_path, "html")
    with pytest.raises(ExportError, match="No document"):
        export_document(tmp_path, "docx")


def test_without_pandoc_the_error_says_how_to_install_it(tmp_path, monkeypatch):
    (tmp_path / "document.md").write_text("# x\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ExportError, match="winget install JohnMacFarlane.Pandoc"):
        export_document(tmp_path, "docx")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_a_word_document_with_contents_and_sources(tmp_path):
    (tmp_path / "document.md").write_text(doc.build("Işık hızı?", "tr", CHAPTERS, SYNTHESIS, SOURCES), encoding="utf-8")
    out = export_document(tmp_path, "docx")
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "Işık hızı?" in xml and "Kaynakça" in xml and "Speed of light" in xml and 'w:type="page"' in xml
