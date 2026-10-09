"""Data tools against a local HTTP server that imitates Kiwix and arXiv."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from local_ai.agent import data

ZIM_V1 = b"old zim content"
ZIM_V2 = b"new zim content, longer than the old one" * 1000

OAI_PAGE_1 = b"""<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
<record><header><identifier>oai:arXiv.org:2401.00001</identifier></header><metadata>
<arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>2401.00001</id><created>2024-01-02</created>
<authors><author><keyname>Doe</keyname><forenames>Jane</forenames></author></authors>
<title>Quantum
  gravity</title><categories>hep-th</categories><abstract> Abstract one. </abstract></arXiv></metadata></record>
<record><header status="deleted"><identifier>x</identifier></header></record>
<resumptionToken cursor="0">TOKEN2</resumptionToken>
</ListRecords></OAI-PMH>"""

OAI_PAGE_2 = b"""<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
<record><header><identifier>oai:arXiv.org:2401.00002</identifier></header><metadata>
<arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>2401.00002</id><created>2024-01-03</created>
<authors><author><keyname>Roe</keyname></author></authors>
<title>Dark matter</title><categories>astro-ph.CO</categories><abstract>Abstract two.</abstract></arXiv></metadata></record>
<resumptionToken cursor="1"></resumptionToken>
</ListRecords></OAI-PMH>"""


def catalog(base):
    entries = "".join(
        f"""<entry><title>Wikipedia {d}</title><name>wikipedia_en_physics</name><flavour>mini</flavour>
        <language>eng</language><updated>{d}-01T00:00:00Z</updated>
        <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
              href="{base}/zim/wikipedia_en_physics_mini_{d}.zim.meta4" length="{len(body)}"/></entry>"""
        for d, body in (("2024-01", ZIM_V1), ("2025-06", ZIM_V2))
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


class Handler(BaseHTTPRequestHandler):
    calls = []
    fail_once = set()

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        Handler.calls.append(self.path)
        base = f"http://{self.headers['Host']}"
        if url.path == "/catalog/v2/entries":
            return self._send(200, catalog(base))
        if url.path == "/oai2":
            if "oai" in Handler.fail_once:
                Handler.fail_once.discard("oai")
                return self._send(503, b"", {"Retry-After": "0"})
            return self._send(200, OAI_PAGE_2 if q.get("resumptionToken") == ["TOKEN2"] else OAI_PAGE_1)
        if url.path.startswith("/zim/"):
            body = ZIM_V2 if "2025-06" in url.path else ZIM_V1
            if url.path.endswith(".sha256"):
                return self._send(200, f"{hashlib.sha256(body).hexdigest()}  x.zim\n".encode())
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].rstrip("-"))
                return self._send(206, body[start:])
            return self._send(200, body)
        if url.path.startswith("/pdf/"):
            return self._send(200, b"%PDF-1.4 fake")
        self._send(404)


@pytest.fixture
def server(monkeypatch, tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setenv("LOCAL_AI_KIWIX_CATALOG", base)
    monkeypatch.setenv("LOCAL_AI_ARXIV_OAI", f"{base}/oai2")
    monkeypatch.setenv("LOCAL_AI_ARXIV_PDF", f"{base}/pdf")
    monkeypatch.setenv("LOCAL_AI_WORKSPACE", str(tmp_path / "ws"))
    Handler.calls.clear()
    yield base
    httpd.shutdown()


def test_catalog_search(server):
    books = data.zim_catalog("physics")
    assert [b["file"] for b in books] == [
        "wikipedia_en_physics_mini_2024-01.zim",
        "wikipedia_en_physics_mini_2025-06.zim",
    ]
    assert books[0]["url"].endswith(".zim") and books[1]["size"] == len(ZIM_V2)


def test_download_resumes_and_verifies(server, tmp_path):
    dest = tmp_path / "zim" / "w.zim"
    dest.parent.mkdir()
    (dest.parent / "w.zim.part").write_bytes(ZIM_V2[:1000])  # interrupted earlier
    data.download(f"{server}/zim/wikipedia_en_physics_mini_2025-06.zim", dest, quiet=True)
    assert dest.read_bytes() == ZIM_V2 and not (dest.parent / "w.zim.part").exists()


def test_checksum_mismatch_removes_partial(server, tmp_path):
    dest = tmp_path / "bad.zim"
    (tmp_path / "bad.zim.part").write_bytes(b"corrupt" * 10)  # wrong prefix, Range continues after it
    with pytest.raises(RuntimeError, match="Checksum"):
        data.download(f"{server}/zim/wikipedia_en_physics_mini_2025-06.zim", dest, quiet=True)
    assert not dest.exists() and not (tmp_path / "bad.zim.part").exists()


def test_zim_update_replaces_old_release(server, tmp_path):
    folder = tmp_path / "zim"
    folder.mkdir()
    (folder / "wikipedia_en_physics_mini_2024-01.zim").write_bytes(ZIM_V1)
    messages = data.zim_update(folder, quiet=True)
    assert messages == ["updated: wikipedia_en_physics_mini_2024-01.zim -> wikipedia_en_physics_mini_2025-06.zim"]
    assert [p.name for p in folder.iterdir()] == ["wikipedia_en_physics_mini_2025-06.zim"]
    assert data.zim_update(folder, quiet=True) == ["up to date: wikipedia_en_physics_mini_2025-06.zim"]


def test_arxiv_harvest_pages_retry_and_incremental(server, tmp_path):
    out = tmp_path / "meta.jsonl"
    Handler.fail_once.add("oai")  # one 503 with Retry-After
    stats = data.arxiv_harvest(out, "physics", delay=0, quiet=True)
    assert stats["total"] == 2 and stats["pages"] == 2
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["title"] == "Quantum gravity" and rows[0]["authors"] == ["Doe, Jane"]
    assert rows[0]["abstract"] == "Abstract one."
    # second run continues from the saved date and doesn't duplicate
    stats = data.arxiv_harvest(out, "physics", delay=0, quiet=True)
    assert stats["total"] == 2 and stats["new"] == 0
    assert any("from=" in c for c in Handler.calls if c.startswith("/oai2?verb=ListRecords&metadataPrefix"))


def test_arxiv_pdfs_and_status(server, tmp_path):
    paths = data.arxiv_pdfs(["2401.00001", "hep-th/9901001"], tmp_path / "pdfs", delay=0, quiet=True)
    assert [p.name for p in paths] == ["2401.00001.pdf", "hep-th_9901001.pdf"]
    text = data.status(tmp_path / "nozim")
    assert "arXiv metadata: none" in text and "Library index" in text
