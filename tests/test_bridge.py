import pytest

from local_ai.agent.config import load_mcp_config
from local_ai.agent.mcp_bridge import MCPBridge, public_tool_name

pytestmark = pytest.mark.anyio

LIBRARY_TOOLS = {"search_library", "read_library", "wikipedia", "docs", "search_arxiv", "read_arxiv"}


def test_public_tool_name():
    assert public_tool_name("library", "search_library") == "library__search_library"
    assert public_tool_name("my server", "do.it") == "my_server__do_it"


async def test_bridge_exposes_and_calls_tools(mcp_config):
    async with MCPBridge(load_mcp_config(mcp_config)) as bridge:
        names = set(bridge.tools)
        assert {f"library__{t}" for t in LIBRARY_TOOLS} == {n for n in names if n.startswith("library__")}
        assert "notebook__add_source" in names and "compute__python" in names
        assert all(t["type"] == "function" for t in bridge.openai_tools())

        # the library folder is indexed on the first search
        text, err = await bridge.call("library__search_library", {"query": "speed of light"})
        assert not err and "speed_of_light.md" in text and "299792458" in text
        chunk_id = text.split("[chunk ", 1)[1].split("]", 1)[0]
        text, err = await bridge.call("library__read_library", {"source": int(chunk_id)})
        assert not err and "Roemer" in text
        text, err = await bridge.call("library__read_library", {"source": "speed_of_light.md"})
        assert not err and "(1 parts)" in text
        text, err = await bridge.call("library__read_library", {"source": "../mcp.json"})
        assert not err and text.startswith("Not found")  # nothing outside the library can be read

        text, err = await bridge.call("library__nope", {})
        assert err and "unknown tool" in text
        text, err = await bridge.call("compute__python", "{not json")
        assert err and "not valid JSON" in text

        text, err = await bridge.call(
            "compute__check_units",
            {"expression": "m*v**2", "variables": {"m": "kg", "v": "m/s"}, "expected_unit": "J"},
        )
        assert not err and text.startswith("OK"), text
        text, err = await bridge.call("compute__python", {"code": "x = 21"})
        text, err = await bridge.call("compute__python", {"code": "print(x * 2)"})
        assert (text.strip(), err) == ("42", False)

        await bridge.call("notebook__create_project", {"name": "t"})
        text, err = await bridge.call("notebook__read_note", {"project": "t", "title": "missing"})
        assert err


def test_load_config_filters(mcp_config):
    assert [s.name for s in load_mcp_config(mcp_config, only=["library"])] == ["library"]
