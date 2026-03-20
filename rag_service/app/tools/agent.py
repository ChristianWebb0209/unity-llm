"""
Pydantic AI agent for Unity RAG + tools.
The agent runs the tool loop; tools delegate to execute_tool which handles backend vs client execution.
System prompt is defined in app.prompts so all backends share one config.
"""
import os
from typing import Any, List, Optional

from pydantic_ai import Agent, RunContext

from ..prompts import UNITY_AGENT_SYSTEM_PROMPT
from .deps import UnityQueryDeps
from .runner import execute_tool

# Default model (Responses API). Override via env OPENAI_MODEL or per-run.
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def _run_tool(ctx: RunContext[UnityQueryDeps], name: str, **kwargs: Any) -> Any:
    """Forward to execute_tool; used by all tool wrappers."""
    return execute_tool(name, dict(kwargs), ctx.deps)


# --- Tool wrappers: same names and parameters as ToolDef for schema compatibility ---

def create_file(
    ctx: RunContext[UnityQueryDeps],
    path: str,
    content: str = "",
    overwrite: bool = False,
) -> Any:
    """Create an empty file at path. Prefer create_file(path) then write_file(path, content)."""
    return _run_tool(ctx, "create_file", path=path, content=content, overwrite=overwrite)


def write_file(ctx: RunContext[UnityQueryDeps], path: str, content: str) -> Any:
    """Overwrite a file with new content. Creates the file if it does not exist."""
    return _run_tool(ctx, "write_file", path=path, content=content)


def append_to_file(ctx: RunContext[UnityQueryDeps], path: str, content: str) -> Any:
    """Append content to the end of a file. Creates the file if it does not exist."""
    return _run_tool(ctx, "append_to_file", path=path, content=content)


def apply_patch(
    ctx: RunContext[UnityQueryDeps],
    path: str,
    old_string: str = "",
    new_string: str = "",
    diff: str = "",
) -> Any:
    """Edit a file by replacing old_string with new_string, or pass a unified diff."""
    return _run_tool(ctx, "apply_patch", path=path, old_string=old_string, new_string=new_string, diff=diff)


def create_script(
    ctx: RunContext[UnityQueryDeps],
    path: str,
    language: str = "gdscript",
    extends_class: str = "Node",
    initial_content: str = "",
    template: str = "",
) -> Any:
    """Create a new GDScript or C# script file with one extends line and initial content."""
    return _run_tool(
        ctx,
        "create_script",
        path=path,
        language=language,
        extends_class=extends_class,
        initial_content=initial_content,
        template=template,
    )


def create_node(
    ctx: RunContext[UnityQueryDeps],
    node_type: str,
    scene_path: str = "",
    parent_path: str = "/root",
    node_name: str = "",
) -> Any:
    """Add a new node to a scene. Omit scene_path (or use 'current') for the current open scene."""
    return _run_tool(
        ctx,
        "create_node",
        node_type=node_type,
        scene_path=scene_path,
        parent_path=parent_path,
        node_name=node_name,
    )


def modify_attribute(
    ctx: RunContext[UnityQueryDeps],
    target_type: str,
    attribute: str,
    value: Any,
    scene_path: str = "",
    node_path: str = "",
    path: str = "",
) -> Any:
    """Set an attribute on a target (node or import)."""
    return _run_tool(
        ctx,
        "modify_attribute",
        target_type=target_type,
        attribute=attribute,
        value=value,
        scene_path=scene_path,
        node_path=node_path,
        path=path,
    )


def read_file(ctx: RunContext[UnityQueryDeps], path: str) -> Any:
    """Read the full contents of a project file. Use before editing or when the user asks what's in a file."""
    return _run_tool(ctx, "read_file", path=path)


def delete_file(ctx: RunContext[UnityQueryDeps], path: str) -> Any:
    """Delete a file from the project (Assets/...)."""
    return _run_tool(ctx, "delete_file", path=path)


def list_directory(
    ctx: RunContext[UnityQueryDeps],
    path: str = "Assets/",
    recursive: bool = False,
    max_entries: int = 250,
    max_depth: int = 6,
) -> Any:
    """List files and folders in a directory under Assets/."""
    return _run_tool(ctx, "list_directory", path=path, recursive=recursive, max_entries=max_entries, max_depth=max_depth)


def search_files(
    ctx: RunContext[UnityQueryDeps],
    query: str,
    root_path: str = "Assets/",
    extensions: Optional[List[str]] = None,
    max_matches: int = 50,
) -> Any:
    """Search for a text query inside project files under Assets/ (grep)."""
    return _run_tool(
        ctx,
        "search_files",
        query=query,
        root_path=root_path,
        extensions=extensions or [],
        max_matches=max_matches,
    )


def list_files(
    ctx: RunContext[UnityQueryDeps],
    path: str = "Assets/",
    recursive: bool = True,
    extensions: Optional[List[str]] = None,
    max_entries: int = 500,
) -> Any:
    """List file paths under Assets/ by optional extension(s), without searching file contents."""
    return _run_tool(
        ctx,
        "list_files",
        path=path,
        recursive=recursive,
        extensions=extensions or [],
        max_entries=max_entries,
    )


def read_import_options(ctx: RunContext[UnityQueryDeps], path: str) -> Any:
    """Read the .import file for a resource (e.g. Assets/icon.svg)."""
    return _run_tool(ctx, "read_import_options", path=path)


def lint_file(ctx: RunContext[UnityQueryDeps], path: str) -> Any:
    """Run the Unity script linter on a project file."""
    return _run_tool(ctx, "lint_file", path=path)


def project_structure(
    ctx: RunContext[UnityQueryDeps],
    prefix: str = "Assets/",
    max_paths: int = 300,
    max_depth: Optional[int] = None,
) -> Any:
    """List indexed project file paths under a prefix."""
    return _run_tool(ctx, "project_structure", prefix=prefix, max_paths=max_paths, max_depth=max_depth)


def find_scripts_by_extends(ctx: RunContext[UnityQueryDeps], extends_class: str) -> Any:
    """Find script files that extend a given class (e.g. CharacterBody2D, Node)."""
    return _run_tool(ctx, "find_scripts_by_extends", extends_class=extends_class)


def find_references_to(ctx: RunContext[UnityQueryDeps], res_path: str) -> Any:
    """Find files that reference a given path (e.g. a scene or script)."""
    return _run_tool(ctx, "find_references_to", res_path=res_path)


def get_recent_changes(ctx: RunContext[UnityQueryDeps], limit: int = 20) -> Any:
    """Return the last N edit events (what files were recently created/modified by the AI)."""
    return _run_tool(ctx, "get_recent_changes", limit=limit)


def grep_search(
    ctx: RunContext[UnityQueryDeps],
    pattern: str = "",
    query: str = "",
    root_path: str = "Assets/",
    extensions: Optional[List[str]] = None,
    max_matches: int = 100,
    use_regex: bool = True,
) -> Any:
    """Search project files with a regex or exact pattern."""
    return _run_tool(
        ctx,
        "grep_search",
        pattern=pattern or query,
        query=query,
        root_path=root_path,
        extensions=extensions or [],
        max_matches=max_matches,
        use_regex=use_regex,
    )


def fetch_url(ctx: RunContext[UnityQueryDeps], url: str) -> Any:
    """Fetch the content of a URL via HTTP GET (e.g. docs, API page)."""
    return _run_tool(ctx, "fetch_url", url=url)


def run_terminal_command(ctx: RunContext[UnityQueryDeps], command: str, timeout_seconds: int = 60) -> Any:
    """Run a shell command on the user's machine. Captures stdout, stderr, and exit code."""
    return _run_tool(ctx, "run_terminal_command", command=command, timeout_seconds=timeout_seconds)


def run_unity_headless(
    ctx: RunContext[UnityQueryDeps],
    scene_path: str = "",
    script_path: str = "",
    timeout_seconds: int = 30,
) -> Any:
    """Run Unity headlessly with a scene or script path."""
    return _run_tool(
        ctx,
        "run_unity_headless",
        scene_path=scene_path,
        script_path=script_path,
        timeout_seconds=timeout_seconds,
    )


def run_scene(ctx: RunContext[UnityQueryDeps], scene_path: str, timeout_seconds: int = 30) -> Any:
    """Run a Unity scene headlessly and capture output/errors."""
    return _run_tool(ctx, "run_scene", scene_path=scene_path, timeout_seconds=timeout_seconds)


def get_node_tree(ctx: RunContext[UnityQueryDeps], scene_path: str = "") -> Any:
    """Get the scene tree structure for the current open scene or a given .tscn path."""
    return _run_tool(ctx, "get_node_tree", scene_path=scene_path)


def get_signals(
    ctx: RunContext[UnityQueryDeps],
    node_type: str = "",
    script_path: str = "",
) -> Any:
    """List available signals for a node type or script."""
    return _run_tool(ctx, "get_signals", node_type=node_type, script_path=script_path)


def connect_signal(
    ctx: RunContext[UnityQueryDeps],
    scene_path: str,
    node_path: str,
    signal_name: str,
    callable_target: str = "",
) -> Any:
    """Connect a signal on a node to a callable."""
    return _run_tool(
        ctx,
        "connect_signal",
        scene_path=scene_path,
        node_path=node_path,
        signal_name=signal_name,
        callable_target=callable_target,
    )


def get_export_vars(
    ctx: RunContext[UnityQueryDeps],
    script_path: str = "",
    node_path: str = "",
    scene_path: str = "",
) -> Any:
    """List @export variables for a script or node."""
    return _run_tool(
        ctx,
        "get_export_vars",
        script_path=script_path,
        node_path=node_path,
        scene_path=scene_path,
    )


def search_asset_library(
    ctx: RunContext[UnityQueryDeps],
    filter: str = "",
    query: str = "",
    unity_version: str = "4.2",
    max_results: int = 20,
) -> Any:
    """Search the Unity Asset Library for addons/plugins by keyword."""
    return _run_tool(
        ctx,
        "search_asset_library",
        filter=filter or query,
        query=query,
        unity_version=unity_version,
        max_results=max_results,
    )


def get_project_settings(ctx: RunContext[UnityQueryDeps]) -> Any:
    """Read project.unity settings (key-value by section)."""
    return _run_tool(ctx, "get_project_settings")


def get_autoloads(ctx: RunContext[UnityQueryDeps]) -> Any:
    """List autoloaded singletons from project.unity."""
    return _run_tool(ctx, "get_autoloads")


def get_input_map(ctx: RunContext[UnityQueryDeps]) -> Any:
    """Read the input map from project.unity (action names and bound keys)."""
    return _run_tool(ctx, "get_input_map")


def check_errors(ctx: RunContext[UnityQueryDeps]) -> Any:
    """Return the current editor Errors/Warnings panel content."""
    return _run_tool(ctx, "check_errors")


# All tools in the same order as get_registered_tools() for consistency.
UNITY_AGENT_TOOLS = [
    create_file,
    write_file,
    append_to_file,
    apply_patch,
    create_script,
    create_node,
    modify_attribute,
    read_file,
    delete_file,
    list_directory,
    search_files,
    list_files,
    read_import_options,
    lint_file,
    project_structure,
    find_scripts_by_extends,
    find_references_to,
    get_recent_changes,
    grep_search,
    fetch_url,
    run_terminal_command,
    run_unity_headless,
    run_scene,
    get_node_tree,
    get_signals,
    connect_signal,
    get_export_vars,
    search_asset_library,
    get_project_settings,
    get_autoloads,
    get_input_map,
    check_errors,
]


def create_unity_agent(
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Agent[UnityQueryDeps, str]:
    """
    Create the Unity RAG agent with tools.
    Uses OpenAI Responses API. Pass api_key/base_url for per-run overrides (e.g. from plugin settings).
    """
    model_name = model or DEFAULT_MODEL
    # Use openai-responses: prefix so Pydantic AI uses Responses API
    model_id = f"openai-responses:{model_name}" if ":" not in model_name else model_name
    agent = Agent(
        model_id,
        deps_type=UnityQueryDeps,
        instructions=UNITY_AGENT_SYSTEM_PROMPT,
        tools=UNITY_AGENT_TOOLS,
    )
    return agent


# Singleton agent (default env); main can replace or use create_unity_agent for overrides.
unity_agent = create_unity_agent()
