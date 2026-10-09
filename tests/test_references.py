import json
import sqlite3

import pymupdf

from local_ai.servers.bibtex import format_entry, make_key, parse_bibtex, pdf_paths_from_file_field
from local_ai.servers.common import HashEmbedder
from local_ai.servers.library import importers
from local_ai.servers.library.server import Library, format_hit

BIB = r"""
@string{prl = "Physical Review Letters"}
@comment{ignored {nested} }
@article{einstein1905,
  author = {Einstein, Albert and Planck, Max},
  title = {{Zur Elektrodynamik} bewegter K\"orper},
  journal = prl,
  year = 1905,
  doi = {10.1002/andp.19053221004},
  file = {Full Text PDF:papers/einstein.pdf:application/pdf},
  abstract = "Special relativity",
  note = prl # { extra},
}
@misc{broken, title = {no closing brace
@book{feynman1964, author = "Feynman, Richard", title = "The Feynman Lectures", year = "1964"}
"""


def make_pdf(path, text):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text)
    doc.save(path)


def test_parse_bibtex():
    entries = {e["ID"]: e for e in parse_bibtex(BIB)}
    assert set(entries) >= {"einstein1905", "feynman1964"}
    e = entries["einstein1905"]
    assert e["journal"] == "Physical Review Letters" and e["year"] == "1905"
    assert e["abstract"] == "Special relativity"
    assert e["note"] == "Physical Review Letters extra"


def test_file_field_forms():
    assert pdf_paths_from_file_field("Full Text:/a/b.pdf:application/pdf;Snapshot:/a/c.html:text/html") == ["/a/b.pdf"]
    assert pdf_paths_from_file_field(r":C\:\\docs\\x.pdf:PDF") == [r"C:\docs\x.pdf"]
    assert pdf_paths_from_file_field("/plain/path.pdf") == ["/plain/path.pdf"]


def test_make_key_and_format():
    assert make_key(["Planck, Max"], 1901, "On the law of distribution") == "planck1901distribution"
    assert make_key("Işık Yılmaz", "2020", "Kuantum") == "yilmaz2020kuantum"
    entry = format_entry("article", "k", {"title": "A & B {x}", "year": "2020", "doi": ""})
    assert "title = {A \\& B \\{x\\}}" in entry and "doi" not in entry


def test_bibtex_import_indexes_pdf_with_metadata(tmp_path):
    (tmp_path / "papers").mkdir()
    make_pdf(tmp_path / "papers" / "einstein.pdf", "Moving bodies and the electrodynamics of light.")
    bib = tmp_path / "refs.bib"
    bib.write_text(BIB)
    records = importers.from_bibtex(bib)
    by_key = {r["citekey"]: r for r in records}
    assert by_key["einstein1905"]["pdf"] is not None and by_key["feynman1964"]["pdf"] is None
    assert by_key["einstein1905"]["title"] == "Zur Elektrodynamik bewegter Körper"

    lib = Library(tmp_path / "lib.sqlite", zim_paths=[], embedder=HashEmbedder())
    stats = lib.index_records(records)
    assert stats["indexed"] == 2 and stats["abstract_only"] == 1
    hit = lib.search("electrodynamics of moving bodies", sources="pdf")[0]
    text = format_hit(hit)
    assert "Einstein, Albert; Planck, Max (1905)" in text and "citekey: einstein1905" in text
    assert lib.index_records(records)["unchanged"] == 2


def make_zotero(data_dir, pdf_name):
    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    conn.executescript(
        f"""
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
        CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
        CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
        CREATE TABLE itemAttachments (itemID INT, parentItemID INT, contentType TEXT, path TEXT);
        CREATE TABLE deletedItems (itemID INT);
        INSERT INTO items VALUES (1, 'PARENT01'), (2, 'ATTACH02'), (3, 'GONE0003'), (4, 'ATTACH04');
        INSERT INTO fields VALUES (1, 'title'), (2, 'date'), (3, 'DOI');
        INSERT INTO itemDataValues VALUES (1, 'Black-body radiation'), (2, '1901-03-01'), (3, '10.1/xyz');
        INSERT INTO itemData VALUES (1, 1, 1), (1, 2, 2), (1, 3, 3);
        INSERT INTO creators VALUES (1, 'Max', 'Planck');
        INSERT INTO itemCreators VALUES (1, 1, 1, 0);
        INSERT INTO itemAttachments VALUES (2, 1, 'application/pdf', 'storage:{pdf_name}');
        INSERT INTO itemAttachments VALUES (4, 3, 'application/pdf', 'storage:deleted.pdf');
        INSERT INTO deletedItems VALUES (3);
        """
    )
    conn.commit()
    conn.close()


def test_zotero_import(tmp_path):
    (tmp_path / "storage" / "ATTACH02").mkdir(parents=True)
    make_pdf(tmp_path / "storage" / "ATTACH02" / "planck.pdf", "Energy elements of black body radiation.")
    make_zotero(tmp_path, "planck.pdf")
    records = importers.from_zotero(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["authors"] == ["Planck, Max"] and r["year"] == "1901" and r["doi"] == "10.1/xyz"
    assert r["citekey"] == "planck1901black" and r["pdf"].name == "planck.pdf"


def test_arxiv_jsonl_filters(tmp_path):
    path = tmp_path / "meta.jsonl"
    rows = [
        {
            "id": "2401.00001",
            "title": "Quantum gravity",
            "authors": ["Doe, Jane"],
            "abstract": "QG.",
            "categories": "hep-th gr-qc",
            "published": "2024-01-02",
        },
        {
            "id": "1901.00002",
            "title": "Old cats",
            "authors": "A. B, C. D",
            "abstract": "Cats.",
            "categories": "q-bio.PE",
            "published": "2019-01-05",
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert len(importers.from_arxiv_jsonl(path)) == 2
    sel = importers.from_arxiv_jsonl(path, categories=["hep-th"], since="2020-01-01")
    assert [r["arxiv_id"] for r in sel] == ["2401.00001"] and sel[0]["year"] == "2024"
    lib = Library(tmp_path / "lib.sqlite", zim_paths=[], embedder=HashEmbedder())
    lib.index_records(sel)
    hits = lib.search("quantum gravity", sources="arxiv")
    assert hits[0]["kind"] == "arxiv" and hits[0]["url"] == "https://arxiv.org/abs/2401.00001"
    assert lib.search("quantum gravity", sources="pdf") == []
