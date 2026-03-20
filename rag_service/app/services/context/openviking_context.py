"""
OpenViking context and memory-core integration.

Provides per-chat session memory: commit conversation turns for automatic
memory extraction (6 categories), and retrieve relevant memories via find().
When openviking is not installed or OPENVIKING_ENABLED is false, all functions
no-op (return empty, do not commit).
"""

import os
import re
from typing import Any, Dict, List, Optional

_openviking_clients: Dict[str, Any] = {}
_openviking_base_path: Optional[str] = None
_openviking_enabled: Optional[bool] = None

_OpenViking = None


def _is_enabled() -> bool:
    global _openviking_enabled
    if _openviking_enabled is not None:
        return _openviking_enabled
    try:
        import openviking  # noqa: F401
    except ImportError:
        _openviking_enabled = False
        return False
    env = os.getenv("OPENVIKING_ENABLED", "").strip().lower()
    _openviking_enabled = env in ("1", "true", "yes")
    return _openviking_enabled


def _get_base_path() -> str:
    global _openviking_base_path
    if _openviking_base_path is not None:
        return _openviking_base_path
    override = os.getenv("OPENVIKING_PATH", "").strip()
    if override:
        _openviking_base_path = os.path.abspath(override)
        return _openviking_base_path
    # From app/services/context/ -> up to rag_service root, then data/openviking
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "openviking")
    )
    _openviking_base_path = root
    return root


def _safe_chat_id(chat_id: str) -> str:
    """Sanitize chat_id for use as a directory name."""
    if not chat_id or not isinstance(chat_id, str):
        return "default"
    safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", chat_id.strip())
    return safe or "default"


def _get_client_for_chat(chat_id: str):
    """Get or create an OpenViking client for this chat (one data dir per chat)."""
    global _OpenViking, _openviking_clients
    if not _is_enabled():
        return None
    if _OpenViking is None:
        try:
            from openviking import OpenViking as OV
            _OpenViking = OV
        except ImportError:
            return None
    key = _safe_chat_id(chat_id)
    if key in _openviking_clients:
        return _openviking_clients[key]
    base = _get_base_path()
    session_dir = os.path.join(base, "sessions", key)
    os.makedirs(session_dir, exist_ok=True)
    try:
        client = _OpenViking(path=session_dir)
        _openviking_clients[key] = client
        return client
    except Exception:
        return None


def get_or_create_session(chat_id: str) -> Any:
    """
    Get or create an OpenViking session for this chat_id.
    Returns the session object or None if OpenViking is disabled/unavailable.
    """
    client = _get_client_for_chat(chat_id)
    if client is None:
        return None
    try:
        return client.session()
    except Exception:
        return None


def add_turn_and_commit(
    chat_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    """
    Add the given messages (list of {"role": "user"|"assistant", "content": "..."})
    to the chat's session and commit to trigger memory extraction.
    No-op if OpenViking is disabled or commit fails.
    """
    session = get_or_create_session(chat_id)
    if session is None:
        return
    try:
        for m in messages:
            role = (m.get("role") or "user").strip().lower()
            content = m.get("content") or m.get("text") or ""
            if content:
                session.add(role=role, content=str(content))
        session.commit()
    except Exception:
        pass


def find_memories(
    chat_id: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Retrieve relevant memories for this chat from OpenViking (semantic search).
    Returns list of {"uri": str, "abstract": str, "overview": str or None, "content": str or None}.
    """
    client = _get_client_for_chat(chat_id)
    if client is None or not query or not query.strip():
        return []
    try:
        results = client.find(query.strip(), top_k=top_k)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    if not hasattr(results, "resources") and not isinstance(results, list):
        return []
    resources = getattr(results, "resources", results) or []
    for i, ctx in enumerate(resources):
        if i >= top_k:
            break
        item: Dict[str, Any] = {"uri": getattr(ctx, "uri", "") or str(ctx)}
        if hasattr(ctx, "abstract"):
            item["abstract"] = getattr(ctx, "abstract", "") or ""
        else:
            item["abstract"] = ""
        if hasattr(ctx, "overview"):
            item["overview"] = getattr(ctx, "overview", None)
        else:
            item["overview"] = None
        if hasattr(ctx, "content"):
            item["content"] = getattr(ctx, "content", None)
        else:
            item["content"] = None
        out.append(item)
    return out


def ensure_openviking_data_dir() -> None:
    """Create the OpenViking base data dir if enabled (call from lifespan)."""
    if not _is_enabled():
        return
    path = _get_base_path()
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "sessions"), exist_ok=True)
