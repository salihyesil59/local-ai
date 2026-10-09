"""ZIM helpers: HTML to text, sections, Wikipedia and docs lookups against small test archives."""

import pytest
from libzim.reader import Archive

from local_ai.servers.library import zim
from test_library import make_zim


def test_html_to_text_keeps_tex_and_code():
    text = zim.html_to_text(
        "<h2>Equation</h2><p>Energy <math><semantics><mi>E</mi>"
        '<annotation encoding="application/x-tex">E = mc^2</annotation></semantics></math>.</p>'
        '<div class="navbox">navigation junk</div><pre>def f():\n    return 1</pre>'
    )
    assert "## Equation" in text and "$E = mc^2$" in text
    assert "navigation junk" not in text
    assert "```\ndef f():\n    return 1\n```" in text


def test_superscripts_kept_footnotes_dropped():
    text = zim.html_to_text(
        "<p>h = 6.62607015×10<sup>−34</sup> J⋅Hz<sup>−1</sup>"
        '<sup id="cite_ref-1" class="reference"><a href="#cite_note-1">[1]</a></sup>.</p>'
    )
    assert "10^−34 J⋅Hz^−1" in text and "[1]" not in text


def test_find_section():
    page = "intro\n\n## Usage\nuse it\n\n##### pathlib.Path.glob\nglob docs\n\n##### pathlib.Path.rglob\nrglob docs"
    section = zim.find_section(page, "Path.glob")
    assert section.startswith("##### pathlib.Path.glob") and "rglob docs" not in section
    assert zim.find_section(page, "usage").startswith("## Usage\nuse it")  # a ## section holds its entries
    assert zim.find_section(page, "missing") is None


@pytest.fixture
def archives(tmp_path, monkeypatch):
    path = tmp_path / "wikipedia_en_test_2026-01.zim"
    make_zim(path)
    archive = Archive(path)
    loaded = [("wikipedia_en_test_2026-01", ["eng"], archive), ("devdocs_en_test_2026-01", ["eng"], archive)]
    monkeypatch.setattr(zim, "_archives", loaded)
    return loaded


def test_wikipedia_offline_and_sections(archives):
    page = zim.wikipedia("speed of light")
    assert page.startswith("# Speed of light (offline archive: wikipedia_en_test_2026-01)") and "299792458" in page
    history = zim.wikipedia("speed of light", section="History")
    assert "Roemer" in history and "299792458" not in history


def test_docs_filters_by_library(archives):
    assert "299792458" in zim.docs("speed of light", library="test")
    assert zim.docs("speed of light", library="numpy").startswith("No offline documentation for 'numpy'")
