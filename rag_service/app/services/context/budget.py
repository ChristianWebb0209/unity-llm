"""
Context budget: token estimation, per-block trimming/compression, priority hierarchy,
and deciding when to remove context (fill_target_ratio, drop lowest-priority blocks).
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Priority hierarchy (lower = more important, dropped last when context fills).
# Order: env → task → session_memory → active_file → current_scene_scripts → related
# → recent → errors → extras
PRIORITY_ENV = 0
PRIORITY_TASK = 1
PRIORITY_SESSION_MEMORY = 2
PRIORITY_ACTIVE_FILE = 3
PRIORITY_CURRENT_SCENE_SCRIPTS = 4
PRIORITY_RELATED = 5
PRIORITY_RECENT = 6
PRIORITY_ERRORS = 7
PRIORITY_EXTRAS = 8

MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    "gpt-4.1-mini": 32768,
}


def get_context_limit(model: str) -> int:
    return int(MODEL_CONTEXT_LIMITS.get(model, 32768))


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4.0))


@dataclass(frozen=True)
class ContextUsage:
    model: str
    limit_tokens: int
    estimated_prompt_tokens: int

    @property
    def percent(self) -> float:
        if self.limit_tokens <= 0:
            return 0.0
        return min(1.0, self.estimated_prompt_tokens / float(self.limit_tokens))


def build_context_usage(model: str, parts: List[str]) -> ContextUsage:
    limit_toks = get_context_limit(model)
    est = sum(estimate_tokens(p) for p in parts if p)
    return ContextUsage(model=model, limit_tokens=limit_toks, estimated_prompt_tokens=est)


def trim_text_to_tokens(text: str, max_tokens: int) -> str:
    """Simple truncation to fit token budget."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 200)] + "\n\n[...truncated for context budget...]\n"


def _extract_symbol_summary(text: str, max_items: int = 60) -> str:
    """Extract symbol lines (extends, func, var, etc.) for compression."""
    lines = text.splitlines()
    keep: List[str] = []
    patterns = (
        "extends ", "class_name ", "signal ", "enum ", "@export",
        "const ", "var ", "func ", "static func ", "class ",
        "using ", "namespace ", "public ", "private ", "#include",
    )
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith(patterns):
            keep.append(ln)
            if len(keep) >= max_items:
                break
    return "\n".join(keep)


def compress_text(text: str, max_tokens: int) -> str:
    """Compression: symbols + head + tail; no LLM."""
    if max_tokens <= 0:
        return ""
    head_lines, tail_lines = 60, 40
    lines = text.splitlines()
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:]) if len(lines) > head_lines else ""
    symbols = _extract_symbol_summary(text, max_items=80)
    out = "\n".join([
        "[compressed summary]",
        "== Key symbols ==",
        symbols or "(none detected)",
        "",
        "== File start ==",
        head,
        "",
        "== File end ==",
        tail,
    ]).strip()
    return trim_text_to_tokens(out, max_tokens)


def fit_block_text(text: str, max_tokens: int) -> Tuple[str, str]:
    """Returns (fitted_text, mode) where mode is 'as_is'|'truncated'|'compressed'."""
    if estimate_tokens(text) <= max_tokens:
        return text, "as_is"
    if estimate_tokens(text) > int(max_tokens * 1.35):
        return compress_text(text, max_tokens), "compressed"
    return trim_text_to_tokens(text, max_tokens), "truncated"


@dataclass(frozen=True)
class ContextBlock:
    key: str
    title: str
    priority: int
    max_tokens: int
    text: str


def dedupe_by_signature(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Keep first occurrence of each text body. items are (signature, text)."""
    seen: set[str] = set()
    out: List[Tuple[str, str]] = []
    for sig, txt in items:
        if sig in seen:
            continue
        seen.add(sig)
        out.append((sig, txt))
    return out


def blocks_to_user_content(
    blocks: List[ContextBlock],
    limit: Optional[int] = None,
    reserve: int = 4096,
    fill_target_ratio: float = 1.0,
) -> Tuple[str, Dict[str, Any]]:
    """
    Trim each block to its budget; drop lowest-priority blocks until total <= target_cap.
    target_cap = (limit - reserve) * fill_target_ratio. When fill_target_ratio is 0.5,
    context is capped at 50% so the first things dropped are extras.
    Returns (user_content, debug_info). debug_info includes "log": [str] for the context decision log.
    """
    rendered: List[str] = []
    debug: Dict[str, Any] = {"blocks": [], "dropped": [], "log": []}
    log = debug["log"]
    target_cap: Optional[int] = None
    if limit is not None and limit > reserve:
        available = limit - reserve
        target_cap = max(1024, int(available * fill_target_ratio))

    for b in blocks:
        fitted, mode = fit_block_text(b.text, b.max_tokens)
        est = estimate_tokens(fitted)
        debug["blocks"].append({
            "key": b.key,
            "title": b.title,
            "max_tokens": b.max_tokens,
            "estimated_tokens": est,
            "mode": mode,
            "included": True,
        })
        if fitted:
            rendered.append(f"\n## {b.title}\n{fitted}\n")
            log.append(f"Included: «{b.title}» ({est} tokens, mode={mode})")
        else:
            log.append(f"Skipped: «{b.title}» (empty after fit)")

    combined = "\n".join(rendered).strip()
    total_est = estimate_tokens(combined)
    debug["estimated_total_tokens"] = total_est

    cap = target_cap if target_cap is not None else sum(b.max_tokens for b in blocks)
    if target_cap is not None:
        log.append(f"Target cap: {cap} tokens (limit={limit}, reserve={reserve}, fill_ratio={fill_target_ratio})")
    while len(rendered) > 1 and total_est > cap:
        if rendered:
            last = rendered[-1]
            dropped_block_title = last.split("\n", 1)[0].replace("## ", "").strip() if last.startswith("## ") else ""
            debug["dropped"].append(dropped_block_title)
            # Mark the corresponding block as not included (same index as last in rendered).
            block_idx = len(rendered) - 1
            if block_idx < len(debug["blocks"]):
                debug["blocks"][block_idx]["included"] = False
            log.append(f"Dropped: «{dropped_block_title}» (over cap)")
        rendered.pop()
        combined = "\n".join(rendered).strip()
        total_est = estimate_tokens(combined)
        debug["estimated_total_tokens"] = total_est

    log.append(f"Final context: {total_est} estimated tokens across {len(rendered)} blocks")
    return combined, debug
