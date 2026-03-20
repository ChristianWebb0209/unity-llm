"""
Conversation history context.

The chat in the editor plugin stores the full conversation locally. If the plugin
sends recent turns (e.g. in request_context.extra["conversation_history"]), we can
include them here so the model has dialogue continuity. When to add: always when
provided. When to remove: same as other low-priority blocks when context fills.
"""

from typing import Any, Dict, List, Optional


def build_conversation_context(messages: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """
    Build a single string block from recent conversation turns for context.
    messages: list of {"role": "user"|"assistant"|"system", "content": "..."}.
    Returns None if not provided or empty; otherwise a formatted string.
    """
    if not messages:
        return None
    parts: List[str] = []
    for m in messages[-20:]:  # last 20 messages cap
        role = (m.get("role") or "user").strip().lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            parts.append(f"[User]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        else:
            parts.append(f"[{role}]\n{content}")
    if not parts:
        return None
    return "\n\n---\n\n".join(parts)
