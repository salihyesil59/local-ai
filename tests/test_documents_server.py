"""The documents server: Markdown from the model becomes a PDF or Word file, and a chat is always offered it."""

import json
import shutil
import sys
import zipfile

import pytest

from local_ai.servers.documents import server as docs

MARKDOWN = "# Kara delik\n\nSchwarzschild yarıçapı $r_s = \\frac{2GM}{c^2}$.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"


def test_empty_content_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="Markdown"):
        docs.make_pdf("  ", title="x")


def test_without_pandoc_the_error_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(Exception, match="winget install JohnMacFarlane.Pandoc"):
        docs.make_docx(MARKDOWN, title="Kara delik")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_a_word_file_in_the_outputs_folder_and_a_second_one_keeps_the_first(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    first = docs.make_docx(MARKDOWN, title="Kara delik", filename="Kara Delik Notu")
    assert first == f"Saved DOCX: {tmp_path / 'outputs' / 'kara-delik-notu.docx'}"
    xml = zipfile.ZipFile(tmp_path / "outputs" / "kara-delik-notu.docx").read("word/document.xml").decode("utf-8")
    assert "Kara delik" in xml and "<m:oMath" in xml  # the formula is a Word equation
    second = docs.make_docx(MARKDOWN, title="Kara delik", filename="Kara Delik Notu")
    assert second.endswith("kara-delik-notu-2.docx")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_a_pdf(tmp_path, monkeypatch):
    from local_ai.servers.export import pdf_engine

    if not pdf_engine():
        pytest.skip("no LaTeX engine")
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path))
    assert docs.make_pdf(MARKDOWN, title="Kara delik").startswith("Saved PDF: ")
    assert (tmp_path / "outputs" / "kara-delik.pdf").read_bytes()[:4] == b"%PDF"
    both = docs.make_pdf(MARKDOWN, title="Kara delik", filename="ikisi", also_docx=True).splitlines()
    assert both == [f"Saved PDF: {tmp_path / 'outputs' / 'ikisi.pdf'}", f"Saved DOCX: {tmp_path / 'outputs' / 'ikisi.docx'}"]
    assert zipfile.is_zipfile(tmp_path / "outputs" / "ikisi.docx")


@pytest.mark.anyio
async def test_a_chat_is_offered_the_documents_tools_and_told_how_to_use_them(tmp_path):
    from local_ai.core.mcp_bridge import MCPBridge, load_mcp_config
    from local_ai.research.config import AgentSettings
    from local_ai.web.runs import Run, run_chat
    from test_web_code import ScriptedClient

    env = {"LOCAL_AI_WORKSPACE": str(tmp_path)}
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"documents": {
        "command": sys.executable, "args": ["-m", "local_ai.servers.documents.server"], "env": env}}}))  # fmt: skip
    client = ScriptedClient([("Hazır.", [])])
    run = Run(id="c", question="PDF olarak ver", project="", mode="chat")
    async with MCPBridge(load_mcp_config(config)) as bridge:
        await run_chat(run, "PDF olarak ver", {}, AgentSettings(model="test"), client, bridge)
    offered = {t["function"]["name"] for t in client.requests[0]["tools"]}
    assert offered == {"documents__make_pdf", "documents__make_docx"}
    assert "make_pdf" in client.requests[0]["messages"][0]["content"]  # the system prompt says when to use it
