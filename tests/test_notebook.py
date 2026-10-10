import pytest

from local_ai.servers.notebook.server import Notebook, slugify


@pytest.fixture
def nb(tmp_path):
    return Notebook(tmp_path)


def test_slugify_handles_turkish():
    assert slugify("Işık hızı nedir?") == "isik-hizi-nedir"
    assert slugify("../../etc") == "etc"
    assert slugify("???") == "untitled"


def test_notes_roundtrip(nb):
    project = nb.create_project("Işık hızı")
    nb.write_note(project, "Finding 1", "first")
    nb.append_note(project, "Finding 1", "second")
    assert nb.read_note(project, "Finding 1") == "first\n\nsecond"
    assert nb.list_notes(project) == ["finding-1"]


def test_unknown_project_rejected(nb):
    with pytest.raises(ValueError):
        nb.write_note("missing", "x", "y")


def test_paths_stay_inside_root(nb, tmp_path):
    project = nb.create_project("../../outside")
    nb.write_note(project, "../../escape", "x")
    assert all(p.resolve().is_relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert not (tmp_path.parent / "escape.md").exists()


def test_sources_numbered_and_deduplicated(nb):
    project = nb.create_project("p")
    assert nb.add_source(project, "1706.03762", "Attention Is All You Need", "arxiv") == 1
    assert nb.add_source(project, "Speed of light", "Speed of light", "Wikipedia", quote="c = 299792458 m/s") == 2
    assert nb.add_source(project, "1706.03762", "Attention Is All You Need", "arxiv", quote="q") == 1
    sources = nb.list_sources(project)
    assert [s["n"] for s in sources] == [1, 2]
    assert sources[0]["quotes"] == ["q"]
    assert sources[1]["origin"] == "wikipedia"


def test_report_gets_generated_bibliography(nb):
    project = nb.create_project("p")
    nb.add_source(project, "a", "Source A", "pdf", "a.pdf p. 3")
    report = nb.save_report(project, "# Answer\n\nText [1].\n\n## References\n\n[1] made up")
    assert "made up" not in report
    assert "[1] Source A (pdf: a.pdf p. 3)" in report
    assert nb.read_report(project) == report


def test_memory_remember_recall_forget(tmp_path):
    from local_ai.servers.common import HashEmbedder
    from local_ai.servers.notebook.memory import Memory

    mem = Memory(tmp_path / "m.sqlite", embedder=HashEmbedder())
    a = mem.remember("The speed of light in vacuum is 299792458 m/s.", sources="Wikipedia: Speed of light")
    assert mem.remember("The speed of light in vacuum is 299792458 m/s.") == a  # deduplicated
    mem.remember("Answer in Turkish with SI units.", kind="preference")
    mem.remember("Hydrogen ground state energy is -13.6 eV.")
    hits = mem.recall("speed of light")
    assert hits[0]["id"] == a and hits[0]["sources"] == "Wikipedia: Speed of light"
    assert [p["text"] for p in mem.preferences()] == ["Answer in Turkish with SI units."]
    assert mem.forget(a) and not any(h["id"] == a for h in mem.recall("speed of light"))


def test_skills_front_matter_and_override(tmp_path):
    from local_ai.servers.notebook.skills import load_skills, skill_dirs

    builtin = load_skills([skill_dirs()[0]])
    assert {"derivation", "fermi-estimate", "dimensional-analysis", "literature-review"} <= set(builtin)
    assert builtin["fermi-estimate"]["description"]
    assert builtin["paper-summary"]["limits"] == {"max_steps": 30, "max_tool_chars": 12000, "max_tokens": 16384}
    assert builtin["derivation"]["limits"] == {}
    custom = tmp_path / "skills"
    custom.mkdir()
    (custom / "mine.md").write_text("---\nname: derivation\ndescription: my version\n---\nDo it my way.")
    merged = load_skills([skill_dirs()[0], custom])
    assert merged["derivation"]["body"] == "Do it my way."


def test_source_metadata_in_bibliography(nb):
    project = nb.create_project("p")
    nb.add_source(project, "x.pdf", "Black-body radiation", "pdf", "page 3")
    nb.add_source(project, "x.pdf", "Black-body radiation", "pdf", authors="Planck, Max", year="1901", doi="10.1/x")
    src = nb.list_sources(project)[0]
    assert src["authors"] == "Planck, Max" and src["doi"] == "10.1/x"
    assert "[1] Planck, Max (1901). Black-body radiation (pdf: page 3) doi:10.1/x" in nb.bibliography(project)
