"""A tool the model can call, and the check of the arguments it sends."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def parse_arguments(arguments: dict | str | None) -> tuple[dict | None, str | None]:
    """The arguments of a tool call as a dict, or the error text for the model: (arguments, None) or (None, error)."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as e:
            return None, f"Error: tool arguments are not valid JSON ({e}). Send a JSON object."
    if not isinstance(arguments, dict):
        return None, "Error: tool arguments must be a JSON object."
    return arguments, None
