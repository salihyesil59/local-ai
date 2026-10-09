from local_ai.agent.tools import ToolRoles

LIBRARY = [
    "library__search_library",
    "library__read_library",
    "library__wikipedia",
    "library__docs",
    "library__search_arxiv",
    "library__read_arxiv",
]
COMPUTE = ["compute__python", "compute__check_units", "compute__solve"]


def test_roles_from_the_package_servers():
    roles = ToolRoles.detect(LIBRARY + COMPUTE + ["notebook__write_note", "web__search", "web__fetch_content"])
    assert roles.get("search") == "library__search_library"  # not the web server's `search`
    assert roles.get("read") == "library__read_library"
    assert roles.get("python") == "compute__python"
    assert roles.get("arxiv") == "library__search_arxiv"
    guide = roles.guide()
    assert guide.index("library__search_library") < guide.index("library__wikipedia") < guide.index("compute__python")
    assert "web__" not in guide and "notebook__" not in guide


def test_other_servers_and_overrides():
    roles = ToolRoles.detect(["other__semantic_search", "other__read_chunk", "other__run_python"])
    assert (roles.get("search"), roles.get("read"), roles.get("python")) == (
        "other__semantic_search",
        "other__read_chunk",
        "other__run_python",
    )
    assert roles.get("check_units") is None
    roles = ToolRoles.detect(LIBRARY + COMPUTE + ["x__python"], {"python": "x__python", "arxiv": "missing__tool"})
    assert roles.get("python") == "x__python" and roles.get("arxiv") == "library__search_arxiv"
