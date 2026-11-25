import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Modules read LOCAL_AI_* settings at import time: keep the test run away from the real workspace and data.
os.environ["LOCAL_AI_WORKSPACE"] = tempfile.mkdtemp(prefix="local-ai-tests-")
os.environ["LOCAL_AI_ZIM_DIR"] = ""
os.environ["LOCAL_AI_LIBRARY"] = str(Path(os.environ["LOCAL_AI_WORKSPACE"]) / "library")

SPEED_OF_LIGHT = (
    "Speed of light: c = 299792458 m/s, a universal physical constant.\n\n"
    "The speed of light in vacuum, c, is exactly 299792458 metres per second. It was first measured by Ole Roemer."
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_config(tmp_path):
    """An mcp.json with the package's library (over a one-file test library), notebook and compute servers."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "speed_of_light.md").write_text(SPEED_OF_LIGHT, encoding="utf-8")
    env = {
        "LOCAL_AI_WORKSPACE": str(tmp_path / "workspace"),
        "LOCAL_AI_LIBRARY": str(library),
        "LOCAL_AI_EMBED_BACKEND": "hash",
        "LOCAL_AI_ZIM_DIR": "",
    }

    def server(name):
        return {"command": sys.executable, "args": ["-m", f"local_ai.servers.{name}.server"], "env": env}

    config = {"mcpServers": {name: server(name) for name in ("library", "notebook", "compute")}}
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path
