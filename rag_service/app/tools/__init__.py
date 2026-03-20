"""
Tools package: agent, definitions, runner, and request-scoped deps.
Re-exports for main and other consumers.
"""
from .agent import create_unity_agent, unity_agent
from .deps import UnityQueryDeps
from .definitions import (
    dispatch_tool_call,
    get_openai_tools_payload,
    get_registered_tools,
)
from .runner import execute_tool

__all__ = [
    "UnityQueryDeps",
    "create_unity_agent",
    "unity_agent",
    "dispatch_tool_call",
    "get_openai_tools_payload",
    "get_registered_tools",
    "execute_tool",
]
