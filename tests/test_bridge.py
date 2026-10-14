import pytest

from local_ai.core.mcp_bridge import TOOL_DEFAULTS, MCPBridge, load_mcp_config, public_tool_name

pytestmark = pytest.mark.anyio

LIBRARY_TOOLS = {"search_library", "read_library", "wikipedia", "docs", "search_arxiv", "read_arxiv", "read_pdf"}


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
        TOOL_DEFAULTS.set({"library__search_library": {"scope": "notebooks/none"}})  # a notebook chosen in the UI
        text, err = await bridge.call("library__search_library", {"query": "speed of light"})
        assert "no folder notebooks/none" in text  # the scope the model left out was filled in
        text, err = await bridge.call("library__search_library", {"query": "speed of light", "scope": ""})
        assert "299792458" in text  # given by the model, it wins
        TOOL_DEFAULTS.set(None)

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


async def test_servers_can_be_started_and_stopped_while_others_run(mcp_config):
    from local_ai.core.mcp_bridge import ServerConfig, read_servers, write_server

    servers = load_mcp_config(mcp_config, only=["notebook"])
    broken = ServerConfig(name="broken", command="this-command-does-not-exist-anywhere")
    async with MCPBridge([*servers, broken], strict=False) as bridge:  # the broken one does not stop the others
        assert set(bridge.sessions) == {"notebook"} and "broken" in bridge.errors
        library = load_mcp_config(mcp_config, only=["library"])[0]
        await bridge.connect(library)
        assert bridge.has("library__search_library") and bridge.has("notebook__write_note")
        await bridge.disconnect("library")
        assert not bridge.has("library__search_library") and bridge.has("notebook__write_note")
        text, err = await bridge.call("notebook__list_projects", {})
        assert not err

    # the file: one server replaced or removed, the rest untouched; a disabled server is not loaded
    write_server(mcp_config, "extra", {"command": "x", "args": ["-y"], "disabled": True})
    assert set(read_servers(mcp_config)) == {"library", "notebook", "compute", "extra"}
    assert "extra" not in {s.name for s in load_mcp_config(mcp_config)}
    write_server(mcp_config, "extra", None)
    assert set(read_servers(mcp_config)) == {"library", "notebook", "compute"}

