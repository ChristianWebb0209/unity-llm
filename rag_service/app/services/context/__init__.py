# Context-building services: budget, scene, project, conversation.
# Public API is re-exported from app.context_builder for backward compatibility.

from .viewer import build_context_view

from .budget import (
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
    blocks_to_user_content,
    estimate_tokens,
    fit_block_text,
    get_context_limit,
    trim_text_to_tokens,
)
from .conversation import build_conversation_context
from .project import (
    append_project_file,
    apply_project_patch,
    apply_project_patch_unified,
    build_related_files_context,
    grep_project_files,
    list_project_directory,
    list_project_files,
    read_project_file,
    read_project_godot_ini,
    search_project_files,
    write_project_file,
)
from .scene import (
    build_current_scene_scripts_context,
    extract_extends_from_script,
    parse_tscn_script_paths,
)

__all__ = [
    "append_project_file",
    "apply_project_patch",
    "apply_project_patch_unified",
    "build_context_view",
    "PRIORITY_CURRENT_SCENE_SCRIPTS",
    "PRIORITY_ENV",
    "PRIORITY_ERRORS",
    "PRIORITY_EXTRAS",
    "PRIORITY_RECENT",
    "PRIORITY_RELATED",
    "PRIORITY_SESSION_MEMORY",
    "PRIORITY_TASK",
    "ContextBlock",
    "ContextUsage",
    "build_context_usage",
    "blocks_to_user_content",
    "build_conversation_context",
    "build_current_scene_scripts_context",
    "build_related_files_context",
    "list_project_directory",
    "search_project_files",
    "extract_extends_from_script",
    "estimate_tokens",
    "fit_block_text",
    "get_context_limit",
    "list_project_files",
    "parse_tscn_script_paths",
    "read_project_file",
    "read_project_godot_ini",
    "trim_text_to_tokens",
    "write_project_file",
    "grep_project_files",
]
