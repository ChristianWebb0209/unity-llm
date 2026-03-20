from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..services.asset_library import search_asset_library
from ..services.context import (
    grep_project_files,
    read_project_godot_ini,
)


@dataclass
class ToolDef:
    name: str
    description: str
    # JSON-schema-like parameters shape for OpenAI tools
    parameters: Dict[str, Any]
    # Backend implementation: (args_dict) -> result serializable to JSON
    handler: Callable[[Dict[str, Any]], Any]


# --- Editor tools: executed on the Godot client; backend returns payload only ---

def _editor_payload(name: str, **kwargs: Any) -> Dict[str, Any]:
    """Return a payload that the Godot plugin will execute locally."""
    out: Dict[str, Any] = {"execute_on_client": True, "action": name}
    out.update(kwargs)
    return out


def _tool_create_file(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("create_file", path=path, content=content, overwrite=overwrite)


def _tool_write_file(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("write_file", path=path, content=content)


def _tool_append_to_file(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("append_to_file", path=path, content=content)


def _tool_apply_patch(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    diff = (args.get("diff") or "").strip()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    payload = {"path": path}
    if diff:
        payload["diff"] = diff
    else:
        payload["old_string"] = old_string
        payload["new_string"] = new_string
    return _editor_payload("apply_patch", **payload)


def _tool_create_script(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    language = (args.get("language") or "gdscript").strip().lower()
    extends_class = (args.get("extends_class") or "Node").strip()
    initial_content = args.get("initial_content", "")
    template = (args.get("template") or "").strip().lower()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    if language not in ("gdscript", "csharp"):
        return {"error": "language must be gdscript or csharp", "execute_on_client": False}
    return _editor_payload(
        "create_script",
        path=path,
        language=language,
        extends_class=extends_class,
        initial_content=initial_content,
        template=template or None,
    )


def _normalize_scene_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return p
    if not p.startswith("res://"):
        p = "res://" + p
    return p


def _tool_create_node(args: Dict[str, Any]) -> Dict[str, Any]:
    scene_path_raw = (args.get("scene_path") or "").strip()
    # Empty or "current" means use current open scene (injected by main.py from active_scene_path, or plugin resolves/creates).
    if not scene_path_raw or scene_path_raw.lower() == "current":
        scene_path = "current"
    else:
        scene_path = _normalize_scene_path(scene_path_raw)
    parent_path = (args.get("parent_path") or "/root").strip()
    node_type = (args.get("node_type") or "Node").strip()
    node_name = (args.get("node_name") or "").strip()
    if not node_type:
        return {"error": "node_type is required", "execute_on_client": False}
    return _editor_payload(
        "create_node",
        scene_path=scene_path,
        parent_path=parent_path,
        node_type=node_type,
        node_name=node_name or None,
    )


def _tool_modify_attribute(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generic tool to set an attribute/property. Use target_type to choose what to modify:
    - node: a property on a node in a scene (scene_path, node_path, attribute, value).
    - import: a key in the [params] section of a resource's .import file (path, attribute, value).
    """
    target_type = str(args.get("target_type") or "").strip().lower()
    attribute = str(args.get("attribute") or "").strip()
    value = args.get("value")
    if not target_type or not attribute:
        return {
            "error": "target_type and attribute are required",
            "execute_on_client": False,
        }
    if value is None:
        return {"error": "value is required", "execute_on_client": False}
    if target_type == "node":
        scene_path = _normalize_scene_path(args.get("scene_path") or "")
        node_path = (args.get("node_path") or "").strip()
        if not scene_path or not node_path:
            return {
                "error": "For target_type=node, scene_path and node_path are required",
                "execute_on_client": False,
            }
        return _editor_payload(
            "modify_attribute",
            target_type="node",
            scene_path=scene_path,
            node_path=node_path,
            attribute=attribute,
            value=value,
        )
    if target_type == "import":
        path = str(args.get("path") or "").strip()
        if not path:
            return {
                "error": "For target_type=import, path is required (e.g. res://icon.svg)",
                "execute_on_client": False,
            }
        if not path.startswith("res://"):
            path = "res://" + path
        return _editor_payload(
            "modify_attribute",
            target_type="import",
            path=path,
            attribute=attribute,
            value=value,
        )
    return {
        "error": "target_type must be 'node' or 'import'",
        "execute_on_client": False,
    }


def _tool_read_file(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("read_file", path=path)


def _tool_delete_file(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "").strip()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("delete_file", path=path)


def _tool_lint_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Godot linter on a project file. Executed on the client; result is shown in the editor."""
    path = (args.get("path") or "").strip()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("lint_file", path=path)


def _tool_list_directory(args: Dict[str, Any]) -> Dict[str, Any]:
    path = (args.get("path") or "res://").strip() or "res://"
    recursive = bool(args.get("recursive", False))
    max_entries = int(args.get("max_entries", 250))
    max_depth = int(args.get("max_depth", 6))
    if max_entries < 1:
        max_entries = 1
    if max_entries > 2000:
        max_entries = 2000
    if max_depth < 0:
        max_depth = 0
    if max_depth > 20:
        max_depth = 20
    return _editor_payload(
        "list_directory",
        path=path,
        recursive=recursive,
        max_entries=max_entries,
        max_depth=max_depth,
    )


def _tool_search_files(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    root_path = str(args.get("root_path") or "res://").strip() or "res://"
    extensions = args.get("extensions") or []
    max_matches = int(args.get("max_matches", 50))
    if not query:
        return {"error": "query is required", "execute_on_client": False}
    if max_matches < 1:
        max_matches = 1
    if max_matches > 500:
        max_matches = 500
    if not isinstance(extensions, list):
        extensions = []
    return _editor_payload(
        "search_files",
        query=query,
        root_path=root_path,
        extensions=extensions,
        max_matches=max_matches,
    )


def _tool_list_files(args: Dict[str, Any]) -> Dict[str, Any]:
    """List file paths under res:// by optional extension(s), no content search (glob-style)."""
    path = str(args.get("path") or "res://").strip() or "res://"
    recursive = bool(args.get("recursive", True))
    extensions = args.get("extensions") or []
    max_entries = int(args.get("max_entries", 500))
    if max_entries < 1:
        max_entries = 1
    if max_entries > 2000:
        max_entries = 2000
    if not isinstance(extensions, list):
        extensions = []
    return _editor_payload(
        "list_files",
        path=path,
        recursive=recursive,
        extensions=extensions,
        max_entries=max_entries,
    )


def _tool_read_import_options(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read the .import file for a resource (e.g. res://icon.svg). Returns full content or params section."""
    path = str(args.get("path") or "").strip()
    if not path:
        return {"error": "path is required", "execute_on_client": False}
    return _editor_payload("read_import_options", path=path)


def _tool_project_structure(args: Dict[str, Any]) -> Dict[str, Any]:
    """Server-only: list indexed file paths under a prefix. Requires project_root_abs from context."""
    return {
        "error": "Project structure is available when the editor has a project open (project_root_abs sent). Open a Godot project and try again.",
        "execute_on_client": False,
    }


def _tool_find_scripts_by_extends(args: Dict[str, Any]) -> Dict[str, Any]:
    """Server-only: find scripts that extend a class. Requires project_root_abs from context."""
    return {
        "error": "Find scripts by extends is available when the editor has a project open. Open a Godot project and try again.",
        "execute_on_client": False,
    }


def _tool_find_references_to(args: Dict[str, Any]) -> Dict[str, Any]:
    """Server-only: find files that reference a given path. Requires project_root_abs from context."""
    return {
        "error": "Find references is available when the editor has a project open. Open a Godot project and try again.",
        "execute_on_client": False,
    }


# --- New tools: get_recent_changes, grep_search, fetch_url, run, scene, node tree, signals, etc. ---

def _tool_get_recent_changes(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deprecated: backend edit history was moved to the Godot plugin.

    The plugin now persists edit timeline + revert locally under `user://` via GodotAIEditStore.
    """
    return {
        "success": False,
        "message": "deprecated: edit history is local-only in the Godot plugin (no backend DB).",
        "events": [],
    }


def _tool_grep_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Regex or exact pattern search in project files. Server runs when project open; else client."""
    pattern = str(args.get("pattern") or args.get("query") or "").strip()
    root_path = str(args.get("root_path") or "res://").strip() or "res://"
    extensions = args.get("extensions") or []
    max_matches = min(500, max(1, int(args.get("max_matches", 100))))
    use_regex = bool(args.get("use_regex", True))
    if not pattern:
        return {"error": "pattern or query is required", "execute_on_client": False}
    return _editor_payload(
        "grep_search",
        pattern=pattern,
        root_path=root_path,
        extensions=extensions,
        max_matches=max_matches,
        use_regex=use_regex,
    )


def _tool_fetch_url(args: Dict[str, Any]) -> Dict[str, Any]:
    """Server-only: HTTP GET a URL and return the response text (e.g. docs, web search result)."""
    url = str(args.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return {"success": False, "message": "url is required and must be http(s)://"}
    try:
        import requests
        r = requests.get(url, timeout=30, headers={"User-Agent": "Godot-AI-Assistant/1.0"})
        r.raise_for_status()
        text = r.text
        if len(text) > 100_000:
            text = text[:100_000] + "\n\n[... truncated ...]"
        return {"success": True, "url": url, "content": text, "message": "Fetched %d chars." % len(text)}
    except Exception as e:
        return {"success": False, "url": url, "message": "Fetch failed: %s" % (e,)}


def _tool_run_terminal_command(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a shell command; executed on the Godot client. Captures stdout/stderr and exit code."""
    command = args.get("command") or args.get("cmd")
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    command = str(command or "").strip()
    if not command:
        return {"error": "command is required", "execute_on_client": False}
    timeout_sec = min(300, max(1, int(args.get("timeout_seconds", 60))))
    return _editor_payload("run_terminal_command", command=command, timeout_seconds=timeout_sec)


def _tool_run_godot_headless(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run Godot headlessly (e.g. --path <dir> --script <path>). Executed on client; captures stdout/stderr."""
    scene_or_script = str(args.get("scene_path") or args.get("script_path") or "").strip()
    if not scene_or_script:
        return {"error": "scene_path or script_path is required", "execute_on_client": False}
    timeout_sec = min(120, max(1, int(args.get("timeout_seconds", 30))))
    return _editor_payload(
        "run_godot_headless",
        scene_path=scene_or_script,
        timeout_seconds=timeout_sec,
    )


def _tool_run_scene(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a scene headlessly and capture output. Executed on Godot client."""
    scene_path = str(args.get("scene_path") or "").strip()
    if not scene_path:
        return {"error": "scene_path is required (e.g. res://main.tscn)", "execute_on_client": False}
    if not scene_path.startswith("res://"):
        scene_path = "res://" + scene_path
    timeout_sec = min(120, max(1, int(args.get("timeout_seconds", 30))))
    return _editor_payload("run_scene", scene_path=scene_path, timeout_seconds=timeout_sec)


def _tool_get_node_tree(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get the scene tree structure (current open scene or given .tscn path). Client."""
    scene_path = str(args.get("scene_path") or "").strip()
    return _editor_payload("get_node_tree", scene_path=scene_path or None)


def _tool_get_signals(args: Dict[str, Any]) -> Dict[str, Any]:
    """List signals for a node type or script. Client."""
    node_type = str(args.get("node_type") or "").strip()
    script_path = str(args.get("script_path") or "").strip()
    return _editor_payload("get_signals", node_type=node_type or None, script_path=script_path or None)


def _tool_connect_signal(args: Dict[str, Any]) -> Dict[str, Any]:
    """Connect a signal on a node to a callable. Client."""
    scene_path = str(args.get("scene_path") or "").strip()
    node_path = str(args.get("node_path") or "").strip()
    signal_name = str(args.get("signal_name") or "").strip()
    callable_target = str(args.get("callable_target") or "").strip()
    if not scene_path or not node_path or not signal_name:
        return {"error": "scene_path, node_path, and signal_name are required", "execute_on_client": False}
    return _editor_payload(
        "connect_signal",
        scene_path=scene_path,
        node_path=node_path,
        signal_name=signal_name,
        callable_target=callable_target or None,
    )


def _tool_get_export_vars(args: Dict[str, Any]) -> Dict[str, Any]:
    """List @export variables for a script or node. Client."""
    script_path = str(args.get("script_path") or "").strip()
    node_path = str(args.get("node_path") or "").strip()
    scene_path = str(args.get("scene_path") or "").strip()
    return _editor_payload(
        "get_export_vars",
        script_path=script_path or None,
        node_path=node_path or None,
        scene_path=scene_path or None,
    )


def _tool_search_asset_library(args: Dict[str, Any]) -> Dict[str, Any]:
    """Server-only: search Godot Asset Library for addons/plugins."""
    filter_text = str(args.get("filter") or args.get("query") or "").strip()
    godot_version = str(args.get("godot_version") or "4.2").strip()
    max_results = min(50, max(1, int(args.get("max_results", 20))))
    return search_asset_library(
        filter_text=filter_text or "plugin",
        godot_version=godot_version,
        max_results=max_results,
    )


def _tool_get_project_settings(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return project.godot settings. Server when project open; else client."""
    return _editor_payload("get_project_settings")


def _tool_get_autoloads(args: Dict[str, Any]) -> Dict[str, Any]:
    """List autoloaded singletons from project.godot. Server when project open; else client."""
    return _editor_payload("get_autoloads")


def _tool_get_input_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read input map (action names and bound keys) from project.godot. Server when project open; else client."""
    return _editor_payload("get_input_map")


def _tool_check_errors(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return current editor Errors/Warnings panel content. Client."""
    return _editor_payload("check_errors")


def get_registered_tools() -> List[ToolDef]:
    """
    Return the list of tools available to the LLM.
    This is the single source of truth for backend-side tools for now.
    """
    return [
        # --- Editor tools (executed on Godot client) ---
        ToolDef(
            name="create_file",
            description=(
                "Create an empty file at path. Prefer create_file(path) then write_file(path, content) so you can write in one or more steps. "
                "Omit content or pass empty for create-only. If overwrite is false, the file must not exist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path, e.g. res://scripts/foo.gd"},
                    "content": {"type": "string", "description": "Optional initial content; omit or empty for create-only.", "default": ""},
                    "overwrite": {"type": "boolean", "description": "Overwrite if exists.", "default": False},
                },
                "required": ["path"],
            },
            handler=_tool_create_file,
        ),
        ToolDef(
            name="write_file",
            description=(
                "Overwrite a file with new content. Creates the file if it does not exist. "
                "Use ONLY when replacing the entire file content. Do NOT use for partial edits (use apply_patch instead). "
                "For .gd files: the file already has one 'extends ClassName' at the top; do not add another extends line."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path starting with res:// (e.g., res://scripts/player.gd)."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
            handler=_tool_write_file,
        ),
        ToolDef(
            name="append_to_file",
            description="Append content to the end of a file. Creates the file if it does not exist. Use ONLY for incremental writes at the end of a file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path starting with res:// (e.g., res://scripts/player.gd)."},
                    "content": {"type": "string", "description": "Content to append to the file."},
                },
                "required": ["path", "content"],
            },
            handler=_tool_append_to_file,
        ),
        ToolDef(
            name="apply_patch",
            description=(
                "Edit a file by replacing the first occurrence of old_string with new_string, or pass a unified diff. "
                "Use ONLY for small, targeted edits to existing files. Do NOT use for full rewrites (use write_file). "
                "For .gd files: do not add a second 'extends' line; the script already has one at the top."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path starting with res:// (e.g., res://scripts/player.gd)."},
                    "old_string": {"type": "string", "description": "Exact text to find and replace (omit if using diff)."},
                    "new_string": {"type": "string", "description": "Replacement text (omit if using diff)."},
                    "diff": {"type": "string", "description": "Optional unified diff string instead of old_string/new_string."},
                },
                "required": ["path"],
            },
            handler=_tool_apply_patch,
        ),
        ToolDef(
            name="create_script",
            description=(
                "Create a new GDScript or C# script file with one extends line and initial content. "
                "Use template (e.g. character_2d) to fill boilerplate so you only supply initial_content for the unique logic. "
                "The created file will have exactly one 'extends' at the top; never add another. "
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path starting with res:// (e.g., res://scripts/player.gd)."},
                    "language": {"type": "string", "description": "gdscript or csharp", "enum": ["gdscript", "csharp"], "default": "gdscript"},
                    "extends_class": {"type": "string", "description": "Base class, e.g. Node, CharacterBody2D (ignored if template is set).", "default": "Node"},
                    "initial_content": {"type": "string", "description": "Optional body content; with template this is the unique logic only.", "default": ""},
                    "template": {"type": "string", "description": "Optional boilerplate template to use.", "enum": ["", "character_2d", "character_3d", "control", "area_2d", "area_3d", "node"], "default": ""},
                },
                "required": ["path"],
            },
            handler=_tool_create_script,
        ),
        ToolDef(
            name="create_node",
            description=(
                "Add a new node to a scene. Executes in the Godot editor: opens the scene, adds the node, saves. "
                "ALWAYS attach to the current scene: omit scene_path (or use 'current'). parent_path defaults to /root. "
                "Match the scene dimension: in a 2D scene use Node2D, CharacterBody2D, Sprite2D, CollisionShape2D, etc.; "
                "in a 3D scene use Node3D, CharacterBody3D, MeshInstance3D, etc. Do NOT use 3D types in a 2D scene or vice versa."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scene_path": {"type": "string", "description": "Optional. res:// path to scene, e.g. res://main.tscn (or use 'current' for the open scene)."},
                    "parent_path": {"type": "string", "description": "Node path of parent in scene; default /root (scene root).", "default": "/root"},
                    "node_type": {"type": "string", "description": "Built-in Godot class only: Node, Node2D, Button, Label, CharacterBody2D, Sprite2D, etc."},
                    "node_name": {"type": "string", "description": "Optional name for the new node."},
                },
                "required": ["node_type"],
            },
            handler=_tool_create_node,
        ),
        ToolDef(
            name="modify_attribute",
            description=(
                "Set an attribute/property on a target. Use target_type to choose: "
                "'node' = property on a node in a scene (scene_path, node_path, attribute, value); "
                "'import' = key in the .import file [params] for a resource (path, attribute, value). "
                "Examples: node position, text; import compress (SVG lossless), mipmaps."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "enum": ["node", "import"], "description": "Either 'node' or 'import'."},
                    "attribute": {"type": "string", "description": "Property/key name (e.g. position, compress, text)."},
                    "value": {"description": "New value (number, string, bool, or [x,y] for vectors)."},
                    "scene_path": {"type": "string", "description": "Required if target_type=node. Scene file path starting with res:// (e.g., res://main.tscn)."},
                    "node_path": {"type": "string", "description": "Required if target_type=node. Path to the node inside the scene, e.g. /root/Sprite"},
                    "path": {"type": "string", "description": "Required if target_type=import. Resource path, e.g. res://icon.svg"},
                },
                "required": ["target_type", "attribute", "value"],
            },
            handler=_tool_modify_attribute,
        ),
        ToolDef(
            name="read_file",
            description=(
                "Read the full contents of a project file. Use this whenever you need to see the current "
                "content of a file (e.g. before editing, or when the user asks what's in a file). "
                "You will receive the file content in the tool result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project path starting with res:// (e.g., res://scripts/player.gd).",
                    },
                },
                "required": ["path"],
            },
            handler=_tool_read_file,
        ),
        ToolDef(
            name="delete_file",
            description="Delete a file from the project. Do NOT use this unless requested.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path starting with res:// (e.g., res://scripts/old.gd)."},
                },
                "required": ["path"],
            },
            handler=_tool_delete_file,
        ),
        ToolDef(
            name="list_directory",
            description="List files and folders in a directory under res://.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path, e.g. res:// or res://scripts", "default": "res://"},
                    "recursive": {"type": "boolean", "description": "List recursively.", "default": False},
                    "max_entries": {"type": "integer", "description": "Max number of returned entries.", "default": 250, "minimum": 1, "maximum": 2000},
                    "max_depth": {"type": "integer", "description": "Max recursion depth if recursive.", "default": 6, "minimum": 0, "maximum": 20},
                },
            },
            handler=_tool_list_directory,
        ),
        ToolDef(
            name="search_files",
            description="Search for a text query inside project files under res:// (grep: finds files containing the text).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for inside files."},
                    "root_path": {"type": "string", "description": "Directory to search under.", "default": "res://"},
                    "extensions": {"type": "array", "items": {"type": "string"}, "description": "Optional extension filters like ['.gd','.tscn'].", "default": []},
                    "max_matches": {"type": "integer", "description": "Max number of file matches.", "default": 50, "minimum": 1, "maximum": 500},
                },
                "required": ["query"],
            },
            handler=_tool_search_files,
        ),
        ToolDef(
            name="list_files",
            description=(
                "List file paths under res:// by optional extension(s), without searching file contents. "
                "Use this to find all files of a type (e.g. all .svg, .png, .tscn). Returns paths only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list, e.g. res:// or res://assets", "default": "res://"},
                    "recursive": {"type": "boolean", "description": "List recursively.", "default": True},
                    "extensions": {"type": "array", "items": {"type": "string"}, "description": "Filter by extension, e.g. ['.svg'], ['.png','.jpg']. Omit for all files.", "default": []},
                    "max_entries": {"type": "integer", "description": "Max paths to return.", "default": 500, "minimum": 1, "maximum": 2000},
                },
            },
            handler=_tool_list_files,
        ),
        ToolDef(
            name="read_import_options",
            description=(
                "Read the .import file for a resource (e.g. res://icon.svg). Returns the file content so you can see current import options. "
                "Import options control how Godot imports assets (e.g. SVG compression, texture mipmaps)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Resource path, e.g. res://icon.svg (the .import file read is path.import)."},
                },
                "required": ["path"],
            },
            handler=_tool_read_import_options,
        ),
        ToolDef(
            name="lint_file",
            description=(
                "Run the Godot script linter on a project file (e.g. res://player.gd). "
                "Use when the user asks to lint a file or check for errors. The linter output is shown in the editor."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project path to the script, e.g. res://scripts/foo.gd"},
                },
                "required": ["path"],
            },
            handler=_tool_lint_file,
        ),
        ToolDef(
            name="project_structure",
            description=(
                "List indexed project file paths under a prefix (from the repo index). "
                "Use to see what files exist without reading them (e.g. 'where is Player?' or project layout)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prefix": {"type": "string", "description": "res:// prefix, e.g. res:// or res://scripts", "default": "res://"},
                    "max_paths": {"type": "integer", "description": "Max paths to return.", "default": 300, "minimum": 1, "maximum": 1000},
                    "max_depth": {"type": "integer", "description": "Max path depth (segment count). Omit for no limit.", "minimum": 1, "maximum": 10},
                },
            },
            handler=_tool_project_structure,
        ),
        ToolDef(
            name="find_scripts_by_extends",
            description=(
                "Find script files that extend a given class (e.g. CharacterBody2D, Node). "
                "Returns paths to .gd/.cs files that contain 'extends ClassName'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "extends_class": {"type": "string", "description": "Class name, e.g. CharacterBody2D, Control, Node"},
                },
                "required": ["extends_class"],
            },
            handler=_tool_find_scripts_by_extends,
        ),
        ToolDef(
            name="find_references_to",
            description=(
                "Find files that reference a given path (e.g. a scene or script). "
                "Uses the project index to return paths that reference the target (instances, scripts, res:// refs)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "res_path": {"type": "string", "description": "Target path, e.g. res://player.tscn or res://scripts/player.gd"},
                },
                "required": ["res_path"],
            },
            handler=_tool_find_references_to,
        ),
        # --- Cursor parity + Godot-specific ---
        ToolDef(
            name="get_recent_changes",
            description=(
                "Deprecated. Edit history is persisted locally in the Godot plugin (GodotAIEditStore). "
                "This backend returns an empty result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max number of events to return.", "default": 20, "minimum": 1, "maximum": 50},
                },
            },
            handler=_tool_get_recent_changes,
        ),
        ToolDef(
            name="grep_search",
            description=(
                "Search project files with a regex or exact pattern. Returns file path, line number, and line text for each match. "
                "Use for symbol/pattern search (e.g. function names, class refs). For simple substring 'which files contain X' use search_files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern or literal text to search for (or use query as alias)."},
                    "query": {"type": "string", "description": "Alias for pattern."},
                    "root_path": {"type": "string", "description": "Directory to search under.", "default": "res://"},
                    "extensions": {"type": "array", "items": {"type": "string"}, "description": "Filter by extension, e.g. ['.gd','.tscn'].", "default": []},
                    "max_matches": {"type": "integer", "description": "Max matches to return.", "default": 100, "minimum": 1, "maximum": 500},
                    "use_regex": {"type": "boolean", "description": "If true, pattern is a regex; else literal.", "default": True},
                },
                "required": ["pattern"],
            },
            handler=_tool_grep_search,
        ),
        ToolDef(
            name="fetch_url",
            description=(
                "Fetch the content of a URL via HTTP GET. Use to look up external documentation (e.g. Godot docs, API pages) "
                "when the user asks for docs or API info; this replaces searching an internal doc index."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch (must be http:// or https://)."},
                },
                "required": ["url"],
            },
            handler=_tool_fetch_url,
        ),
        ToolDef(
            name="run_terminal_command",
            description="Run a shell command on the user's machine. Captures stdout, stderr, and exit code. Use for running scripts, godot --headless, or build commands.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "timeout_seconds": {"type": "integer", "description": "Max time to wait.", "default": 60, "minimum": 1, "maximum": 300},
                },
                "required": ["command"],
            },
            handler=_tool_run_terminal_command,
        ),
        ToolDef(
            name="run_godot_headless",
            description=(
                "Run Godot headlessly with a scene or script path. Captures stdout/stderr and exit code. "
                "Enables write-run-observe-fix loop. Use scene_path (or script_path as alias) to specify what to run."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scene_path": {"type": "string", "description": "res:// path to scene or script to run (or use script_path as alias)."},
                    "script_path": {"type": "string", "description": "Alias for scene_path."},
                    "timeout_seconds": {"type": "integer", "description": "Max time to wait.", "default": 30, "minimum": 1, "maximum": 120},
                },
                "required": ["scene_path"],
            },
            handler=_tool_run_godot_headless,
        ),
        ToolDef(
            name="run_scene",
            description="Run a Godot scene headlessly and capture output/errors. Critical for test-driven agent loop.",
            parameters={
                "type": "object",
                "properties": {
                    "scene_path": {"type": "string", "description": "Project path starting with res:// (e.g., res://main.tscn)."},
                    "timeout_seconds": {"type": "integer", "description": "Max time to wait.", "default": 30, "minimum": 1, "maximum": 120},
                },
                "required": ["scene_path"],
            },
            handler=_tool_run_scene,
        ),
        ToolDef(
            name="get_node_tree",
            description="Get the scene tree structure (node names, types, hierarchy) for the current open scene or a given .tscn path.",
            parameters={
                "type": "object",
                "properties": {
                    "scene_path": {"type": "string", "description": "Optional. res://path/to/scene.tscn; omit for current open scene."},
                },
            },
            handler=_tool_get_node_tree,
        ),
        ToolDef(
            name="get_signals",
            description=(
                "List available signals for a node type or script (name, arguments). Use to reason about signal connections. "
                "Provide at least one of node_type (built-in) or script_path (res:// to script)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "description": "Built-in node type, e.g. Button, CharacterBody2D."},
                    "script_path": {"type": "string", "description": "res:// path to script to inspect for signals."},
                },
            },
            handler=_tool_get_signals,
        ),
        ToolDef(
            name="connect_signal",
            description="Connect a signal on a node to a callable (e.g. another node's method).",
            parameters={
                "type": "object",
                "properties": {
                    "scene_path": {"type": "string", "description": "res:// path to the scene."},
                    "node_path": {"type": "string", "description": "Path to the node in the scene."},
                    "signal_name": {"type": "string", "description": "Name of the signal."},
                    "callable_target": {"type": "string", "description": "Target node path and method, e.g. ../Player/on_clicked."},
                },
                "required": ["scene_path", "node_path", "signal_name"],
            },
            handler=_tool_connect_signal,
        ),
        ToolDef(
            name="get_export_vars",
            description="List @export variables for a script or node (name, type, default). Essential for understanding inspector-configurable state.",
            parameters={
                "type": "object",
                "properties": {
                    "script_path": {"type": "string", "description": "res:// path to script."},
                    "scene_path": {"type": "string", "description": "Scene path if inspecting a node's script."},
                    "node_path": {"type": "string", "description": "Node path in scene if inspecting attached script."},
                },
            },
            handler=_tool_get_export_vars,
        ),
        ToolDef(
            name="search_asset_library",
            description=(
                "Search the Godot Asset Library for addons/plugins by keyword. Returns asset title, author, support level, browse URL. "
                "Use when the user asks for a plugin or addon for a feature."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Search keyword (or use query as alias)."},
                    "query": {"type": "string", "description": "Alias for filter."},
                    "godot_version": {"type": "string", "description": "Godot version filter.", "default": "4.2"},
                    "max_results": {"type": "integer", "description": "Max assets to return.", "default": 20, "minimum": 1, "maximum": 50},
                },
                "required": ["filter"],
            },
            handler=_tool_search_asset_library,
        ),
        ToolDef(
            name="get_project_settings",
            description="Read project.godot settings (key-value by section). Use to see display, rendering, or other config.",
            parameters={"type": "object", "properties": {}},
            handler=_tool_get_project_settings,
        ),
        ToolDef(
            name="get_autoloads",
            description="List autoloaded singletons from project.godot (name and path). Agents need to know what is globally available.",
            parameters={"type": "object", "properties": {}},
            handler=_tool_get_autoloads,
        ),
        ToolDef(
            name="get_input_map",
            description="Read the input map from project.godot (action names and bound keys). Use when writing input handling code.",
            parameters={"type": "object", "properties": {}},
            handler=_tool_get_input_map,
        ),
        ToolDef(
            name="check_errors",
            description="Return the current editor Errors/Warnings panel content (script errors, etc.). Godot equivalent of linter output.",
            parameters={"type": "object", "properties": {}},
            handler=_tool_check_errors,
        ),
    ]


def get_openai_tools_payload() -> List[Dict[str, Any]]:
    """
    Convert internal ToolDef objects into the 'tools' payload expected by
    OpenAI Responses tool-calling APIs.
    """
    tools_payload: List[Dict[str, Any]] = []
    for t in get_registered_tools():
        tools_payload.append(
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
        )
    return tools_payload


def dispatch_tool_call(name: str, arguments: Dict[str, Any]) -> Any:
    """
    Execute the backend implementation for a named tool.
    """
    for t in get_registered_tools():
        if t.name == name:
            return t.handler(arguments)
    raise ValueError(f"Unknown tool: {name}")

