"""
Context builder: orchestrates all context sources into ordered blocks.

Areas of concern live in app.services.context:
  - budget: token limits, trimming, priority hierarchy, when to remove context
  - scene: current scene parsing, scene scripts, extends extraction
  - project: project file read/list, related files (repo-index or heuristic)
  - conversation: optional chat history (when plugin sends it)

This module composes them and exposes the same public API for main.py.
"""

from typing import Any, Dict, List, Optional, Tuple

from . import (
    PRIORITY_ACTIVE_FILE,
    PRIORITY_CURRENT_SCENE_SCRIPTS,
    PRIORITY_ENV,
    PRIORITY_ERRORS,
    PRIORITY_EXTRAS,
    PRIORITY_RECENT,
    PRIORITY_RELATED,
    PRIORITY_SESSION_MEMORY,
    PRIORITY_TASK,
    ContextBlock,
    ContextUsage,
    build_context_usage,
    build_current_scene_scripts_context,
    build_related_files_context,
    blocks_to_user_content,
    extract_extends_from_script,
    estimate_tokens,
    fit_block_text,
    get_context_limit,
    list_project_files,
    parse_tscn_script_paths,
    read_project_file,
    trim_text_to_tokens,
)


def build_ordered_blocks(
    *,
    model: str,
    system_instructions: str,
    question: str,
    active_file_path: Optional[str],
    active_file_text: Optional[str],
    errors_text: Optional[str],
    related_files: List[Tuple[str, str]],
    recent_edits: List[str],
    optional_extras: List[str],
    include_system_in_user: bool = False,
    environment_text: Optional[str] = None,
    current_scene_scripts: Optional[List[Tuple[str, str]]] = None,
    exclude_block_keys: Optional[List[str]] = None,
    retrieved_memories: Optional[List[str]] = None,
) -> List[ContextBlock]:
    """
    Compose all context sources into ordered blocks (priority hierarchy).
    Hierarchy: env → task → session_memory → active file → current scene scripts → related
    → recent edits → errors → extras.
    exclude_block_keys: block keys to omit (from context viewer "Don't include next time").
    retrieved_memories: optional OpenViking session memory snippets for this chat.
    """
    excluded = set(exclude_block_keys or [])
    limit = get_context_limit(model)

    env_budget = min(1200, max(600, int(limit * 0.04)))
    task_budget = min(1200, max(300, int(limit * 0.03)))
    session_memory_budget = min(1500, max(400, int(limit * 0.06)))
    file_budget = min(5500, max(1500, int(limit * 0.18)))
    scene_scripts_budget = min(8000, max(2000, int(limit * 0.20)))
    related_budget = min(4500, max(1000, int(limit * 0.14)))
    recent_budget = min(2000, max(400, int(limit * 0.06)))
    err_budget = min(3200, max(600, int(limit * 0.10)))
    extra_budget = min(2400, max(400, int(limit * 0.08)))

    blocks: List[ContextBlock] = []
    off = 1 if include_system_in_user else 0
    if include_system_in_user:
        sys_budget = min(1200, max(400, int(limit * 0.04)))
        blocks.append(
            ContextBlock(
                key="system",
                title="System instructions",
                priority=0,
                max_tokens=sys_budget,
                text=system_instructions.strip(),
            )
        )
    if environment_text and environment_text.strip():
        blocks.append(
            ContextBlock(
                key="environment",
                title="Environment",
                priority=PRIORITY_ENV + off,
                max_tokens=env_budget,
                text=environment_text.strip(),
            )
        )
    blocks.append(
        ContextBlock(
            key="task",
            title="Current task",
            priority=PRIORITY_TASK + off,
            max_tokens=task_budget,
            text=f"User request:\n{question.strip()}",
        )
    )

    if retrieved_memories and "session_memory" not in excluded:
        memory_text = "\n\n".join(retrieved_memories).strip()
        if memory_text:
            blocks.append(
                ContextBlock(
                    key="session_memory",
                    title="Retrieved session memory",
                    priority=PRIORITY_SESSION_MEMORY + off,
                    max_tokens=session_memory_budget,
                    text=memory_text,
                )
            )

    if (active_file_path or active_file_text) and "active_file" not in excluded:
        file_header = (
            f"Active file (user's project; editor-focused): {active_file_path or '(unknown)'}\n"
            f"(This is the script/file the user has open. Edit this when they ask to fix or change the current file.)\n\n"
        )
        blocks.append(
            ContextBlock(
                key="active_file",
                title="Active file",
                priority=PRIORITY_ACTIVE_FILE + off,
                max_tokens=file_budget,
                text=(file_header + (active_file_text or "")).strip(),
            )
        )

    if current_scene_scripts and "current_scene_scripts" not in excluded:
        parts: List[str] = [
            "Scripts attached to nodes in the currently open scene (user's project). Paths are res://.\n"
        ]
        for p, content in current_scene_scripts:
            parts.append(f"\n--- Script in current scene: {p} ---\n{content}")
        blocks.append(
            ContextBlock(
                key="current_scene_scripts",
                title="Current scene scripts",
                priority=PRIORITY_CURRENT_SCENE_SCRIPTS + off,
                max_tokens=scene_scripts_budget,
                text="\n".join(parts).strip(),
            )
        )

    if related_files and "related_files" not in excluded:
        related_header = "Files in the user's project that are structurally related to the active file (res:// paths).\n"
        related_parts = [related_header] + [
            f"\n--- Related file: {p} ---\n{content}" for p, content in related_files
        ]
        blocks.append(
            ContextBlock(
                key="related_files",
                title="Related files (structural proximity)",
                priority=PRIORITY_RELATED + off,
                max_tokens=related_budget,
                text="\n".join(related_parts).strip(),
            )
        )

    if recent_edits:
        recent_header = "Recent edits in the user's project (file path, diff).\n\n"
        blocks.append(
            ContextBlock(
                key="recent_edits",
                title="Recent edits (recency working set)",
                priority=PRIORITY_RECENT + off,
                max_tokens=recent_budget,
                text=(recent_header + "\n\n".join([t for t in recent_edits if t])).strip(),
            )
        )

    if errors_text and "errors" not in excluded:
        err_header = "Lint/editor errors or diagnostics from the user's project (fix these in the files they refer to).\n\n"
        blocks.append(
            ContextBlock(
                key="errors",
                title="Errors / diagnostics",
                priority=PRIORITY_ERRORS + off,
                max_tokens=err_budget,
                text=(err_header + errors_text.strip()).strip(),
            )
        )

    if optional_extras and "extras" not in excluded:
        blocks.append(
            ContextBlock(
                key="extras",
                title="Optional extras",
                priority=PRIORITY_EXTRAS + off,
                max_tokens=extra_budget,
                text="\n".join([e for e in optional_extras if e]).strip(),
            )
        )

    blocks.sort(key=lambda b: b.priority)
    return blocks


__all__ = [
    "build_ordered_blocks",
    "build_context_usage",
    "build_current_scene_scripts_context",
    "build_related_files_context",
    "blocks_to_user_content",
    "extract_extends_from_script",
    "estimate_tokens",
    "fit_block_text",
    "get_context_limit",
    "list_project_files",
    "parse_tscn_script_paths",
    "read_project_file",
    "trim_text_to_tokens",
    "ContextBlock",
    "ContextUsage",
]

