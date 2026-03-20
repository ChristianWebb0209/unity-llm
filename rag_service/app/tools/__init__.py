"""
Tools package: agent, definitions, runner, and request-scoped deps.
Re-exports for main and other consumers.
"""
from .agent import create_godot_agent, godot_agent
from .deps import GodotQueryDeps
from .definitions import (
    dispatch_tool_call,
    get_openai_tools_payload,
    get_registered_tools,
)
from .runner import execute_tool

__all__ = [
    "GodotQueryDeps",
    "create_godot_agent",
    "godot_agent",
    "dispatch_tool_call",
    "get_openai_tools_payload",
    "get_registered_tools",
    "execute_tool",
]
