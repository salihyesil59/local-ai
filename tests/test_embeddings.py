"""Embedding model defaults, query instructions and the guard against mixing vectors of different models."""

import json

from local_ai.servers import common
from local_ai.servers.common import DEFAULT_EMBED_MODEL, Embedder, HashEmbedder
from local_ai.servers.library.server import Library
from local_ai.servers.notebook.memory import Memory


class Recorder(HashEmbedder):
    def __init__(self, model):
        super().__init__()
        self.model = model


def test_default_model_and_query_instruction(monkeypatch):
    monkeypatch.delenv("LOCAL_AI_EMBED_MODEL", raising=False)
    monkeypatch.delenv("LOCAL_AI_EMBED_QUERY_TASK", raising=False)
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"data": [{"index": 0, "embedding": [1.0, 0.0]}]}).encode()

    def urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return Response()

    monkeypatch.setattr(common.urllib.request, "urlopen", urlopen)
    e = Embedder()
    assert e.model == DEFAULT_EMBED_MODEL == "text-embedding-qwen3-embedding-0.6b"
    e.embed(["Kara cisim nedir?"], query=True)
    e.embed(["A black body absorbs all radiation."])
    assert sent[0]["input"][0].startswith("Instruct: ") and sent[0]["input"][0].endswith("\nQuery: Kara cisim nedir?")
    assert sent[1]["input"] == ["A black body absorbs all radiation."]  # documents get no instruction

    monkeypatch.setenv("LOCAL_AI_EMBED_MODEL", "text-embedding-bge-m3")
    assert Embedder().query_task == ""  # other models: no instruction unless configured
    monkeypatch.setenv("LOCAL_AI_EMBED_QUERY_TASK", "")
    monkeypatch.setenv("LOCAL_AI_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    assert Embedder().query_task == ""  # empty string turns it off


def test_library_model_change_disables_dense_until_reembed(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "a.md").write_text("Black-body radiation follows Planck's law.", encoding="utf-8")
    db = tmp_path / "lib.sqlite"
    Library(db, zim_paths=[], embedder=Recorder("model-a")).index_folder(str(papers))

    lib = Library(db, zim_paths=[], embedder=Recorder("model-b"))
    assert lib.stale_model == "model-a" and lib.status()["stale_vectors_from"] == "model-a"
    assert lib.search("Planck law")[0]["title"]  # keyword search still works
    assert lib.reembed() == 1
    assert Library(db, zim_paths=[], embedder=Recorder("model-b")).stale_model is None


def test_memory_model_change_and_reembed(tmp_path):
    path = tmp_path / "m.sqlite"
    Memory(path, embedder=Recorder("model-a")).remember("The speed of light is 299792458 m/s.")
    mem = Memory(path, embedder=Recorder("model-b"))
    assert mem.stale_model == "model-a"
    assert mem.recall("speed of light")  # BM25 still finds it
    assert mem.reembed() == 1
    assert Memory(path, embedder=Recorder("model-b")).stale_model is None
