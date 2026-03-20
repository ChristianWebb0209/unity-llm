"""
Context viewer: build a display model from context blocks and debug info
for the UI (per-chat context viewer tab). Optionally supports exclude/pin
for manual add/remove (stored per chat, sent in request).
"""

from typing import Any, Dict, List

# Max length for content_preview in the view (chars).
PREVIEW_MAX_CHARS = 1200


def build_context_view(
    blocks: List[Any],
    debug: Dict[str, Any],
    *,
    preview_chars: int = PREVIEW_MAX_CHARS,
) -> List[Dict[str, Any]]:
    """
    Build a list of block view dicts for the context viewer UI.
    blocks: list of ContextBlock (key, title, priority, max_tokens, text).
    debug: from blocks_to_user_content (blocks list with key/title/estimated_tokens/mode, dropped list).
    Returns list of {
      "key": str,
      "title": str,
      "priority": int,
      "max_tokens": int,
      "estimated_tokens": int,
      "mode": "as_is"|"truncated"|"compressed",
      "included": bool,
      "content_preview": str,
      "content_length": int,
    }
    """
    dropped_titles = set(debug.get("dropped") or [])
    debug_blocks = {b.get("title"): b for b in (debug.get("blocks") or []) if b.get("title")}

    out: List[Dict[str, Any]] = []
    for b in blocks:
        title = getattr(b, "title", None) or (b.get("title") if isinstance(b, dict) else "")
        key = getattr(b, "key", None) or (b.get("key") if isinstance(b, dict) else "")
        priority = getattr(b, "priority", 0) or (b.get("priority", 0) if isinstance(b, dict) else 0)
        max_tokens = getattr(b, "max_tokens", 0) or (b.get("max_tokens", 0) if isinstance(b, dict) else 0)
        text = getattr(b, "text", "") or (b.get("text", "") if isinstance(b, dict) else "")

        db = debug_blocks.get(title) or {}
        estimated_tokens = int(db.get("estimated_tokens", 0))
        mode = db.get("mode", "as_is")
        included = title not in dropped_titles

        content_preview = text
        if len(content_preview) > preview_chars:
            content_preview = content_preview[: preview_chars] + "\n\n[... truncated for preview ...]"

        out.append({
            "key": key,
            "title": title,
            "priority": priority,
            "max_tokens": max_tokens,
            "estimated_tokens": estimated_tokens,
            "mode": mode,
            "included": included,
            "content_preview": content_preview,
            "content_length": len(text),
        })

    return out
