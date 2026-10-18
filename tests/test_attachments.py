"""Files sent with a message: checked, kept with the chat, read as text and given to the agent of each mode."""

import base64
import io
import shutil
import subprocess
import zipfile

import pymupdf
import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.core import attachments as att  # noqa: E402
from local_ai.core.mcp_bridge import load_mcp_config  # noqa: E402
from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_loop import StreamingClient, reply  # noqa: E402
from test_web import read_events  # noqa: E402
from test_web_chats import FINISHING  # noqa: E402
from test_web_code import ScriptedClient, own_workspace  # noqa: E402,F401

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def zipped(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def docx() -> bytes:
    body = (
        f"<w:document {W}><w:body>"
        "<w:p><w:r><w:t>Kara delik </w:t></w:r><w:r><w:t>nedir?</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>M</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>r_s</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl></w:body></w:document>"
    )
    return zipped({"word/document.xml": body})


def pptx() -> bytes:
    def slide(text):
        return f"<p:sld xmlns:p='p' {A}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>"

    return zipped({"ppt/slides/slide10.xml": slide("Onuncu"), "ppt/slides/slide2.xml": slide("İkinci")})


def xlsx() -> bytes:
    return zipped(
        {
            "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Veri" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/sharedStrings.xml": f"<sst {S}><si><t>kütle</t></si></sst>",
            "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row><c t="s"><v>0</v></c><c><v>2.5</v></c>'
            "</row></sheetData></worksheet>",
        }
    )


def pdf() -> bytes:
    doc = pymupdf.open()
    for text in ("Birinci sayfa", "Second page"):
        doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def url(data: bytes, mime: str = "application/octet-stream") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def test_the_text_of_each_kind_of_file(tmp_path):
    for name, data in (("a.docx", docx()), ("a.pptx", pptx()), ("a.xlsx", xlsx()), ("a.pdf", pdf())):
        (tmp_path / name).write_bytes(data)
    assert att.extract(tmp_path / "a.docx") == ("Kara delik nedir?\nM | r_s", "6 words")
    text, info = att.extract(tmp_path / "a.pptx")
    assert info == "2 slides" and text.index("İkinci") < text.index("Onuncu")  # slide 2 before slide 10
    assert att.extract(tmp_path / "a.xlsx") == ("--- sheet Veri ---\nkütle\t2.5", "1 sheets")
    text, info = att.extract(tmp_path / "a.pdf")
    assert info == "2 pages" and "--- page 2 ---\nSecond page" in text
    (tmp_path / "n.md").write_text("# Not\nsatır", encoding="utf-8")
    assert att.extract(tmp_path / "n.md") == ("# Not\nsatır", "2 lines")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_a_word_file_made_by_pandoc(tmp_path):
    (tmp_path / "in.md").write_text("# Başlık\n\nParagraf metni.\n", encoding="utf-8")
    subprocess.run(["pandoc", str(tmp_path / "in.md"), "-o", str(tmp_path / "out.docx")], check=True)
    text, _ = att.extract(tmp_path / "out.docx")
    assert "Başlık" in text and "Paragraf metni." in text


def test_only_known_files_of_a_sane_size_are_taken():
    assert att.decode(None) == []
    assert att.decode([{"name": "../../x/notes.md", "data": url(b"hi")}]) == [("notes.md", b"hi")]
    bad = [
        [{"name": "a.exe", "data": url(b"MZ")}],
        [{"name": "a.md", "data": "data:text/plain;base64,"}],  # empty
        [{"name": "a.md", "data": "https://example.com/a.md"}],
        [{"name": "a.md", "data": url(b"x")}] * 11,
        "not a list",
    ]
    for files in bad:
        with pytest.raises(ValueError):
            att.decode(files)


def test_what_does_not_fit_is_cut_and_points_to_the_library():
    assert att.shares([10, 1000], 100) == [10, 90]
    items = [
        {"name": "small.txt", "text": "short", "info": "1 lines"},
        {"name": "book.pdf", "text": "x" * 500, "info": "300 pages", "library": "notebooks/ekler-1/book.pdf"},
        {"name": "scan.pdf", "text": "", "info": "1 pages"},
    ]
    text = att.block(items, 105)
    assert "[Attached file: small.txt (1 lines)]\nshort\n[end of small.txt]" in text
    assert "only its first 100 of 500 characters" in text and 'file="notebooks/ekler-1/book.pdf"' in text
    assert 'scope="notebooks/ekler-1"' in text and "no text could be read" in text
    assert att.block([], 100) == ""


def test_a_chat_gets_the_text_and_a_large_file_goes_into_the_library(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_LIBRARY", str(tmp_path / "library"))
    client = ScriptedClient([("Özet.", [])])
    big = ("Kara delikler hakkında uzun bir kitap. " * 2000).encode()
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        files = [{"name": "rapor.docx", "data": url(docx())}, {"name": "kitap.txt", "data": url(big)}]
        assert ui.post("/api/runs", json={"mode": "chat", "question": "x", "files": [files[0] | {"name": "a.exe"}]}).status_code == 400
        run = ui.post("/api/runs", json={"mode": "chat", "question": "Özetle", "files": files}).json()
        events = read_events(ui, run["id"])

        sent = client.requests[0]["messages"][-1]["content"]
        assert sent.startswith("Özetle") and "Kara delik nedir?" in sent and "[end of rapor.docx]" in sent
        assert "kitap.txt" in sent and "read_library" in sent and len(sent) < 45_000
        assert run["question"] == "Özetle" and len(run["files"]) == 2  # the chat shows what was typed
        assert any(e["kind"] == "answer" for e in events)
        copy = tmp_path / "library" / "notebooks" / f"ekler-{run['id']}" / "kitap.txt"
        assert copy.read_bytes() == big  # the whole book, for search_library and read_library

        name = run["files"][0]
        download = ui.get(f"/api/runs/{run['id']}/attachments/{name}")
        assert download.content == docx() and 'filename="rapor.docx"' in download.headers["content-disposition"]
        assert ui.get(f"/api/runs/{run['id']}/attachments/..%2F..%2Findex.json").status_code == 404

        assert ui.delete(f"/api/runs/{run['id']}").json() == {"ok": True}
        assert not copy.parent.exists() and ui.get(f"/api/runs/{run['id']}/attachments/{name}").status_code == 404


def test_a_code_chat_and_a_steering_message_get_the_text(tmp_path):
    client = ScriptedClient(list(FINISHING))
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        body = {"mode": "code", "question": "", "folder": str(tmp_path / "app"),
                "files": [{"name": "slides.pptx", "data": url(pptx())}]}
        run = ui.post("/api/runs", json=body).json()
        read_events(ui, run["id"])
        assert run["question"].startswith("Look at the attached file")
        assert "Onuncu" in str(client.requests[0]["messages"][1]["content"])

        chat = ui.app.state.runs[run["id"]]
        chat.status = "running"
        message = {"question": "Bunu da kullan", "files": [{"name": "tablo.xlsx", "data": url(xlsx())}]}
        assert ui.post(f"/api/runs/{run['id']}/messages", json=message).status_code == 202
        (text, _), = chat.agent.steering
        assert text.startswith("Bunu da kullan") and "kütle\t2.5" in text
        assert chat.events[-1]["text"] == "Bunu da kullan" and len(chat.events[-1]["files"]) == 1
        chat.status = "done"


def test_a_research_gets_the_text_with_its_question(mcp_config):
    client = StreamingClient({"quick": [reply("Cevap.")]})
    settings = AgentSettings(model="test", critic=False, gap_check=False)
    with TestClient(create_app(load_mcp_config(mcp_config), settings, client=client)) as ui:
        body = {"question": "Bu ne diyor?", "quick": True, "sources": False,
                "files": [{"name": "makale.pdf", "data": url(pdf(), "application/pdf")}]}
        run = ui.post("/api/runs", json=body).json()
        read_events(ui, run["id"])
    asked = client.requests["quick"][0]["messages"][1]["content"]
    assert "Bu ne diyor?" in asked and "makale.pdf (2 pages)" in asked and "Birinci sayfa" in asked
