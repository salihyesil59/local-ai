import json
import shutil

import pymupdf
import pytest

from local_ai.servers.export import ExportError, bibtex, export


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    fig = p / "fig.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pix.save(fig)
    (p / "report.md").write_text(
        f"# Answer\n\nLight is fast [1]. Two sources [1, 2]. Range [1-2].\n\n![speed plot]({fig})\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n## References\n\n[1] old"
    )
    sources = [
        {
            "n": 1,
            "source_id": "Speed of light",
            "title": "Speed of light",
            "origin": "wikipedia",
            "locator": "intro",
            "quotes": ["c = 299792458 m/s"],
        },
        {
            "n": 2,
            "source_id": "arXiv:2401.00001",
            "title": "Quantum gravity",
            "origin": "arxiv",
            "locator": "",
            "quotes": [],
            "authors": "Doe, Jane; Roe, Rick",
            "year": "2024",
            "citekey": "doe2024quantum",
        },
    ]
    (p / "sources.json").write_text(json.dumps(sources))
    return p


def test_bibtex_entries(project):
    sources = json.loads((project / "sources.json").read_text(encoding="utf-8"))
    bib, keys = bibtex(sources)
    assert keys == {1: "speed", 2: "doe2024quantum"}
    assert "@article{doe2024quantum," in bib and "author = {Doe, Jane and Roe, Rick}" in bib
    assert "eprint = {2401.00001}" in bib and "@misc{" in bib


def test_html_is_self_contained(project):
    out = export(project, "html")
    page = out.read_text(encoding="utf-8")
    assert "data:image/png;base64," in page and "<table>" in page
    assert 'href="#ref-1"' in page and 'title="Speed of light — c = 299792458 m/s"' in page
    assert page.count('href="#ref-2"') == 2  # [1, 2] and the expanded range [1-2]
    assert "old" not in page  # model-written References replaced


def test_md_and_bib(project):
    assert export(project, "md").read_text(encoding="utf-8").startswith("# Answer")
    assert "@article" in export(project, "bib").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_pandoc_docx_and_tex(project):
    assert export(project, "docx").stat().st_size > 0
    tex = export(project, "tex").read_text(encoding="utf-8")
    assert "\\begin{document}" in tex and "quantum gravity" in tex.lower()


def test_pdf_without_latex_explains(project, monkeypatch):
    import local_ai.servers.export as ex

    monkeypatch.setattr(ex, "_pdf_engine", lambda: None)
    if not shutil.which("pandoc"):
        pytest.skip("pandoc not installed")
    with pytest.raises(ExportError, match="LaTeX engine"):
        export(project, "pdf")


def test_unknown_format_and_missing_report(tmp_path):
    with pytest.raises(ExportError):
        export(tmp_path, "html")
