from pathlib import Path

import pymupdf
import pytest
from libzim.writer import Creator, Hint, Item, StringProvider

from local_ai.servers.common import HashEmbedder
from local_ai.servers.library.server import Library, chunk_text, html_sections


def make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text)
    doc.save(path)


class Article(Item):
    def __init__(self, path, title, html):
        super().__init__()
        self.path_, self.title_, self.html = path, title, html

    def get_path(self):
        return self.path_

    def get_title(self):
        return self.title_

    def get_mimetype(self):
        return "text/html"

    def get_contentprovider(self):
        return StringProvider(self.html)

    def get_hints(self):
        return {Hint.FRONT_ARTICLE: True}


def make_zim(path):
    with Creator(str(path)).config_indexing(True, "eng") as creator:
        creator.set_mainpath("Speed_of_light")
        creator.add_item(
            Article(
                "Speed_of_light",
                "Speed of light",
                "<html><body><h1>Speed of light</h1><p>The speed of light in vacuum is 299792458 metres per second.</p>"
                "<h2>History</h2><p>Ole Roemer estimated it in 1676 from the moons of Jupiter.</p></body></html>",
            )
        )
        creator.add_item(
            Article("Banana", "Banana", "<html><body><p>A banana is an elongated, edible fruit.</p></body></html>")
        )
        for k, v in {
            "Title": "Test",
            "Language": "eng",
            "Creator": "t",
            "Publisher": "t",
            "Description": "test",
            "Name": "test",
            "Date": "2026-01-01",
        }.items():
            creator.add_metadata(k, v)


@pytest.fixture
def lib(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    make_pdf(
        papers / "qm.pdf",
        ["Schrodinger equation describes wave functions.", "The hydrogen atom ground state energy is -13.6 eV."],
    )
    (papers / "notes.md").write_text("Black body radiation follows Planck's law.")
    zim = tmp_path / "wiki.zim"
    make_zim(zim)
    return Library(tmp_path / "lib.sqlite", zim_paths=[str(zim)], embedder=HashEmbedder()), papers


def test_chunking_overlaps_and_respects_size():
    text = ("Sentence number one is here. " * 200).strip()
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) > 5 and all(len(c) <= 500 for c in chunks)


def test_html_sections():
    sections = html_sections("<h1>T</h1><p>a</p><script>x()</script><h2>H</h2><p>b &amp; c</p>")
    assert sections == [("T", "a"), ("H", "b & c")]


def test_index_and_search_pdf(lib):
    library, papers = lib
    stats = library.index_folder(papers)
    assert stats["indexed"] == 2 and stats["chunks"] >= 3
    hits = library.search("hydrogen ground state energy", sources="pdf")
    assert hits[0]["locator"] == "page 2" and "13.6" in hits[0]["text"]
    # unchanged files are skipped, deleted files removed
    assert library.index_folder(papers)["unchanged"] == 2
    (papers / "notes.md").unlink()
    assert library.index_folder(papers)["removed"] == 1


def test_zim_retrieve_then_rank(lib):
    library, _ = lib
    hits = library.search("speed of light vacuum", sources="zim")
    assert hits and hits[0]["kind"] == "zim" and "299792458" in hits[0]["text"]
    # Turkish query, English terms supplied separately
    hits = library.search("ışık hızı", sources="zim", english_query="light speed Roemer")
    assert any("Roemer" in h["text"] for h in hits)


def test_read_chunk_with_neighbors(lib):
    library, papers = lib
    library.index_folder(papers)
    hit = library.search("Schrodinger wave functions", sources="pdf")[0]
    full = library.read_chunk(hit["id"], neighbors=1)
    assert "Schrodinger" in full["text"] and "13.6" in full["text"]


def test_keyword_only_without_embeddings(tmp_path, lib):
    _, papers = lib
    no_embed = HashEmbedder()
    no_embed.available = False
    no_embed.embed = lambda texts, query=False: None
    library = Library(tmp_path / "kw.sqlite", zim_paths=[], embedder=no_embed)
    library.index_folder(papers)
    assert "13.6" in library.search("hydrogen", sources="pdf")[0]["text"]


def test_library_folder_is_indexed_on_search_and_readable(tmp_path, monkeypatch):
    from local_ai.servers.library import server

    papers = tmp_path / "papers"
    (papers / "qm").mkdir(parents=True)
    make_pdf(papers / "qm" / "atoms.pdf", ["Intro page.", "The hydrogen atom ground state energy is -13.6 eV."])
    monkeypatch.setenv("LOCAL_AI_LIBRARY", str(papers))
    library = Library(tmp_path / "auto.sqlite", zim_paths=[], embedder=HashEmbedder())

    hit = library.search("hydrogen ground state")[0]  # no index step: the library folder is picked up here
    assert f"source: {Path('qm') / 'atoms.pdf'}" in server.format_hit(hit)
    page2 = library.read_file(str(Path("qm") / "atoms.pdf"), page=2, pages=1)
    assert "(2 pages)" in page2 and "13.6" in page2 and "Intro" not in page2
    with pytest.raises(FileNotFoundError):
        library.read_file(str(tmp_path / "auto.sqlite"))  # outside the library and not indexed

    (papers / "notes.md").write_text("Black body radiation follows Planck's law.", encoding="utf-8")
    library._auto_checked = 0  # skip the AUTO_INDEX_EVERY wait
    assert "Planck" in library.search("Planck black body")[0]["text"]


def test_auto_index_budget_spreads_a_large_first_index(lib):
    library, papers = lib
    stats = library.index_folder(papers, budget=0)
    assert stats["indexed"] == 1 and stats["pending"] == 1  # one file, then the budget is used up
    assert library.index_folder(papers, budget=0)["pending"] == 0


def test_read_arxiv_extracts_id_and_searches_query(tmp_path, monkeypatch):
    from local_ai.servers.library import arxiv

    pdf_path = tmp_path / "2401.01234.pdf"
    make_pdf(pdf_path, ["Title and abstract", "Algorithm 1: Quantum phase estimation steps...", "Discussion and results."])
    monkeypatch.setattr(arxiv, "cache_dir", lambda name: tmp_path)
    monkeypatch.setattr(arxiv, "http_get", lambda url, timeout=60: b"dummy")

    # Raw id
    res = arxiv.read_arxiv("2401.01234", page=1, pages=1)
    assert "Title and abstract" in res

    # URL format
    res_url = arxiv.read_arxiv("https://arxiv.org/abs/2401.01234", page=2, pages=1)
    assert "Quantum phase estimation" in res_url

    # In-paper query search
    res_query = arxiv.read_arxiv("2401.01234", query="estimation")
    assert "found 'estimation' on pages [2]" in res_query
    assert "Quantum phase estimation" in res_query


def test_read_pdf_reads_a_file_by_its_path(tmp_path, monkeypatch):
    from local_ai.servers.library import arxiv

    paper = tmp_path / "somewhere" / "paper.pdf"
    paper.parent.mkdir()
    make_pdf(paper, ["Title and abstract", "Algorithm 1: Quantum phase estimation steps...", "Discussion."])
    first = arxiv.read_pdf(str(paper), page=1, pages=1)
    assert first.startswith("paper.pdf (3 pages)") and "Title and abstract" in first and "Algorithm 1" not in first
    assert "found 'algorithm' on pages [2]" in arxiv.read_pdf(f'"{paper}"', query="algorithm")  # a quoted path

    monkeypatch.setattr(arxiv, "library_dir", lambda: paper.parent)  # a relative path is a library file
    assert "Discussion." in arxiv.read_pdf("paper.pdf", page=3)
    assert arxiv.read_pdf(str(tmp_path / "nope.pdf")).startswith("Error: no PDF file")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert arxiv.read_pdf(str(tmp_path / "notes.txt")).startswith("Error: no PDF file")


def test_reading_returns_whole_pages_and_says_where_to_go_on(tmp_path):
    from local_ai.servers.library import arxiv

    paper = tmp_path / "long.pdf"
    make_pdf(paper, [f"Page {n} starts. " + f"equation {n} " * 280 + f"Page {n} ends." for n in range(1, 9)])
    reply = arxiv.read_pdf(str(paper), page=1, pages=6)
    shown = [n for n in range(1, 9) if f"--- page {n} ---" in reply]
    assert shown[0] == 1 and 2 <= len(shown) < 6 and len(reply) <= 12000
    last = shown[-1]
    assert f"Page {last} ends." in reply and f"Page {last + 1} starts." not in reply  # no page is cut
    assert reply.endswith(f"Go on with page={last + 1}.]") and f"Pages {last + 1}," in reply
    rest = arxiv.read_pdf(str(paper), page=last + 1, pages=1)
    assert f"Page {last + 1} ends." in rest and "Go on with" not in rest

