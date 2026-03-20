"""
Single entry point for executing tools during the Pydantic AI agent run.
When project_root_abs is set in deps, runs backend logic for file/project tools;
otherwise delegates to dispatch_tool_call (e.g. execute_on_client for the plugin).
"""
from typing import Any, Dict

from .deps import GodotQueryDeps
from .definitions import dispatch_tool_call
from ..services.context import (
    append_project_file,
    apply_project_patch,
    apply_project_patch_unified,
    grep_project_files,
    list_project_directory,
    read_project_godot_ini,
    search_project_files,
    write_project_file,
)
from ..services.context.context_builder import list_project_files, read_project_file
from ..services.repo_indexing import get_inbound_refs, list_indexed_paths


def execute_tool(name: str, arguments: Dict[str, Any], deps: GodotQueryDeps) -> Any:
    """
    Execute a tool by name with the given arguments and request-scoped deps.
    When deps.project_root_abs is set, runs backend logic for supported tools;
    otherwise (or for client-only tools) returns result from dispatch_tool_call.
    """
    project_root_abs = deps.project_root_abs
    active_scene_path = deps.active_scene_path
    read_file_cache = deps.read_file_cache
    args_dict = dict(arguments)

    if name == "read_file" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if path:
            cache_key = (path if path.startswith("res://") else "res://" + path.lstrip("/")).replace("\\", "/")
            if cache_key in read_file_cache:
                content = read_file_cache[cache_key]
                return {
                    "success": True,
                    "path": path,
                    "content": content,
                    "message": "Read (cached): %s (%d chars)" % (path, len(content)),
                }
            content = read_project_file(project_root_abs, path)
            content_str = content or ""
            read_file_cache[cache_key] = content_str
            return {
                "success": True,
                "path": path,
                "content": content_str,
                "message": "Read: %s (%d chars)" % (path, len(content_str)),
            }
        return dispatch_tool_call(name, args_dict)

    if name == "list_files" and project_root_abs:
        path = (args_dict.get("path") or "res://").strip() or "res://"
        recursive = bool(args_dict.get("recursive", True))
        extensions = args_dict.get("extensions") or []
        max_entries = min(2000, max(1, int(args_dict.get("max_entries", 500))))
        paths = list_project_files(
            project_root_abs, path, recursive=recursive,
            extensions=extensions, max_entries=max_entries,
        )
        return {
            "success": True,
            "message": "Listed %d file(s) under %s" % (len(paths), path),
            "path": path,
            "paths": paths,
        }

    if name == "read_import_options" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if path:
            import_path = path if path.endswith(".import") else path + ".import"
            content = read_project_file(project_root_abs, import_path)
            return {
                "success": content is not None,
                "message": "Read import options for %s" % path if content is not None else "No .import file found for: %s" % path,
                "path": path,
                "import_path": import_path,
                "content": content or "",
            }
        return dispatch_tool_call(name, args_dict)

    if name == "list_directory" and project_root_abs:
        path = (args_dict.get("path") or "res://").strip() or "res://"
        recursive = bool(args_dict.get("recursive", False))
        max_entries = min(2000, max(1, int(args_dict.get("max_entries", 250))))
        max_depth = min(20, max(0, int(args_dict.get("max_depth", 6))))
        entries = list_project_directory(
            project_root_abs, path, recursive=recursive,
            max_entries=max_entries, max_depth=max_depth,
        )
        return {
            "success": True,
            "message": "Listed %d entry/entries under %s" % (len(entries), path),
            "path": path,
            "entries": entries,
        }

    if name == "search_files" and project_root_abs:
        query = (args_dict.get("query") or "").strip()
        if not query:
            return dispatch_tool_call(name, args_dict)
        root_path = (args_dict.get("root_path") or "res://").strip() or "res://"
        extensions = args_dict.get("extensions") or []
        max_matches = min(500, max(1, int(args_dict.get("max_matches", 50))))
        results = search_project_files(
            project_root_abs, query, root_path=root_path,
            extensions=extensions, max_matches=max_matches,
        )
        return {
            "success": True,
            "message": "Found %d file(s) containing %r" % (len(results), query),
            "query": query,
            "results": results,
        }

    if name == "project_structure" and project_root_abs:
        import os
        if os.getenv("ENABLE_REPO_INDEXING", "false").lower() not in ("1", "true", "yes"):
            return {
                "success": False,
                "error": "Repo indexing tools are disabled in this environment (ENABLE_REPO_INDEXING=0). You cannot list project structure."
            }

        prefix = (args_dict.get("prefix") or "res://").strip() or "res://"
        max_paths = min(1000, max(1, int(args_dict.get("max_paths", 300))))
        max_depth_arg = args_dict.get("max_depth")
        max_depth = int(max_depth_arg) if max_depth_arg is not None else None
        if max_depth is not None:
            max_depth = min(10, max(1, max_depth))
        paths = list_indexed_paths(
            project_root_abs, prefix=prefix, max_paths=max_paths, max_depth=max_depth
        )
        return {
            "success": True,
            "message": "Listed %d path(s) under %s" % (len(paths), prefix),
            "prefix": prefix,
            "paths": paths,
        }

    if name == "find_scripts_by_extends" and project_root_abs:
        extends_class = (args_dict.get("extends_class") or "").strip()
        if not extends_class:
            return dispatch_tool_call(name, args_dict)
        query = "extends " + extends_class
        results = search_project_files(
            project_root_abs, query, root_path="res://",
            extensions=[".gd", ".cs"], max_matches=30,
        )
        paths = [r["path"] for r in results]
        return {
            "success": True,
            "message": "Found %d script(s) extending %s" % (len(paths), extends_class),
            "extends_class": extends_class,
            "paths": paths,
        }

    if name == "find_references_to" and project_root_abs:
        import os
        if os.getenv("ENABLE_REPO_INDEXING", "false").lower() not in ("1", "true", "yes"):
            return {
                "success": False,
                "error": "Repo indexing tools are disabled in this environment (ENABLE_REPO_INDEXING=0). You cannot find references."
            }

        res_path = (args_dict.get("res_path") or "").strip()
        if not res_path:
            return dispatch_tool_call(name, args_dict)
        refs = get_inbound_refs(project_root_abs, res_path, limit=20)
        return {
            "success": True,
            "message": "Found %d file(s) referencing %s" % (len(refs), res_path),
            "res_path": res_path,
            "references": refs,
        }

    if name == "grep_search" and project_root_abs:
        pattern = str(args_dict.get("pattern") or args_dict.get("query") or "").strip()
        if not pattern:
            return dispatch_tool_call(name, args_dict)
        root_path = (args_dict.get("root_path") or "res://").strip() or "res://"
        extensions = args_dict.get("extensions") or []
        max_matches = min(500, max(1, int(args_dict.get("max_matches", 100))))
        use_regex = bool(args_dict.get("use_regex", True))
        matches = grep_project_files(
            project_root_abs,
            pattern,
            root_path=root_path,
            extensions=extensions,
            max_matches=max_matches,
            use_regex=use_regex,
        )
        return {
            "success": True,
            "message": "Found %d match(es)." % len(matches),
            "pattern": pattern,
            "matches": matches,
        }

    if name == "get_project_settings" and project_root_abs:
        ini = read_project_godot_ini(project_root_abs)
        return {
            "success": True,
            "message": "Project settings (project.godot sections).",
            "sections": {k: v for k, v in ini.items()},
        }

    if name == "get_autoloads" and project_root_abs:
        ini = read_project_godot_ini(project_root_abs)
        autoload = ini.get("autoload", {})
        items = [{"name": k, "path": v} for k, v in autoload.items()]
        return {
            "success": True,
            "message": "Autoloads from project.godot.",
            "autoloads": items,
        }

    if name == "get_input_map" and project_root_abs:
        ini = read_project_godot_ini(project_root_abs)
        inp = ini.get("input", {})
        items = [{"action": k, "events": v} for k, v in inp.items()]
        return {
            "success": True,
            "message": "Input map from project.godot.",
            "input_map": items,
        }

    if name == "create_file" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if not path:
            return dispatch_tool_call(name, args_dict)
        content = args_dict.get("content", "") or ""
        overwrite = bool(args_dict.get("overwrite", False))
        return write_project_file(
            project_root_abs, path, content, overwrite=overwrite
        )

    if name == "write_file" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if not path:
            return dispatch_tool_call(name, args_dict)
        content = args_dict.get("content", "") or ""
        return write_project_file(
            project_root_abs, path, content, overwrite=True
        )

    if name == "apply_patch" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if not path:
            return dispatch_tool_call(name, args_dict)
        diff_text = (args_dict.get("diff") or "").strip()
        if diff_text:
            return apply_project_patch_unified(
                project_root_abs, path, diff_text
            )
        old_string = args_dict.get("old_string", "") or ""
        new_string = args_dict.get("new_string", "") or ""
        return apply_project_patch(
            project_root_abs, path, old_string, new_string
        )

    if name == "append_to_file" and project_root_abs:
        path = (args_dict.get("path") or "").strip()
        if not path:
            return dispatch_tool_call(name, args_dict)
        content = args_dict.get("content", "") or ""
        return append_project_file(project_root_abs, path, content)

    if name == "create_node":
        sp = (args_dict.get("scene_path") or "").strip()
        if not sp or sp.lower() == "current":
            args_dict = {**args_dict, "scene_path": active_scene_path or "current"}
        return dispatch_tool_call(name, args_dict)

    return dispatch_tool_call(name, args_dict)
