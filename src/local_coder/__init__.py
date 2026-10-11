"""Compatibility shim re-exporting local_ai.coder."""

import sys
from local_ai.coder import agent, files, server, skills, state, verify
from local_ai.coder.agent import cli, context, llm, loop, prompts, tools

sys.modules["local_coder.files"] = files
sys.modules["local_coder.server"] = server
sys.modules["local_coder.skills"] = skills
sys.modules["local_coder.state"] = state
sys.modules["local_coder.verify"] = verify
sys.modules["local_coder.agent"] = agent
sys.modules["local_coder.agent.cli"] = cli
sys.modules["local_coder.agent.context"] = context
sys.modules["local_coder.agent.llm"] = llm
sys.modules["local_coder.agent.loop"] = loop
sys.modules["local_coder.agent.prompts"] = prompts
sys.modules["local_coder.agent.tools"] = tools

__version__ = "0.1.0"

__all__ = ["files", "server", "skills", "state", "verify", "agent", "__version__"]
