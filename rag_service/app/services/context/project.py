"""
Current project analysis: read files, list files, list directory, search files,
structural dependencies, and related-files context (repo-index or heuristic).
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..repo_indexing import get_related_res_paths, index_repo

_RE_RES_PATH = re.compile(r'res://[^"\'\s\)]+')


def _safe_join(root_abs: str, res_path: str) -> str:
    rp = res_path.replace("\\", "/")
    if rp.startswith("res://"):
        rp = rp[len("res://"):]
    return os.path.abspath(os.path.join(root_abs, rp))


def _ensure_under_root(root_abs: str, abs_path: str) -> bool:
    """Return True if abs_path is under root_abs (no path traversal)."""
    root = os.path.abspath(root_abs)
    path = os.path.abspath(abs_path)
    return path == root or path.startswith(root + os.sep) or path.startswith(root + "/")


_MAX_WRITE_BYTES = 2 * 1024 * 1024  # 2MB, match plugin limit


def write_project_file(
    project_root_abs: str, res_path: str, content: str, overwrite: bool = False
) -> Dict[str, Any]:
    """
    Create or overwrite a file under project_root_abs. res_path is e.g. res://scripts/foo.gd.
    Returns { "success": bool, "path": res_path, "content": new_content, "message": str }.
    """
    if not res_path or not res_path.strip():
        return {"success": False, "path": res_path, "content": "", "message": "path is required"}
    path_norm = res_path.strip().replace("\\", "/")
    if not path_norm.startswith("res://"):
        path_norm = "res://" + path_norm.lstrip("/")
    abs_path = _safe_join(project_root_abs, path_norm)
    if not _ensure_under_root(project_root_abs, abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "path is outside project"}
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return {"success": False, "path": path_norm, "content": "", "message": "content too large"}
    existed = os.path.isfile(abs_path)
    if existed and not overwrite:
        return {"success": False, "path": path_norm, "content": "", "message": "File already exists and overwrite is false"}
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        msg = "Wrote: %s" % path_norm if existed else "Created: %s" % path_norm
        return {"success": True, "path": path_norm, "content": content, "message": msg}
    except Exception as e:
        return {"success": False, "path": path_norm, "content": "", "message": "Failed to write: %s" % (e,)}


def _apply_unified_diff(old_content: str, diff_text: str) -> Optional[str]:
    """
    Apply a unified diff to old_content. Returns new content or None on parse/apply error.
    Handles standard unified diff format (---/+++ headers, @@ hunk headers, then context/-/+ lines).
    """
    lines = diff_text.splitlines()
    hunks: List[Tuple[int, int, List[str]]] = []  # (old_start_1based, old_len, new_lines)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("@@ "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                return None
            try:
                old_part = parts[1].lstrip("-").rstrip(",")
                new_part = parts[2].split(" ", 1)[0].rstrip(",")
                old_start = int(old_part.split(",")[0])
                old_len = int(old_part.split(",")[1]) if "," in old_part else 1
            except (ValueError, IndexError):
                return None
            i += 1
            hunk_new_lines: List[str] = []
            old_count = 0
            while i < len(lines) and not lines[i].startswith("@@"):
                ln = lines[i]
                if ln.startswith(" ") or ln.startswith("+"):
                    hunk_new_lines.append(ln[1:] if len(ln) > 1 else "")
                if ln.startswith(" ") or ln.startswith("-"):
                    old_count += 1
                i += 1
            if old_count != old_len:
                return None
            hunks.append((old_start, old_len, hunk_new_lines))
            continue
        i += 1
    if not hunks:
        return None
    old_lines = old_content.splitlines()
    new_lines: List[str] = []
    pos = 0
    for old_start, old_len, hunk_new in hunks:
        old_idx = max(0, old_start - 1)
        if old_idx > pos:
            new_lines.extend(old_lines[pos:old_idx])
        new_lines.extend(hunk_new)
        pos = old_idx + old_len
    if pos < len(old_lines):
        new_lines.extend(old_lines[pos:])
    result = "\n".join(new_lines)
    if old_content.endswith("\n"):
        result += "\n"
    return result


def apply_project_patch(
    project_root_abs: str, res_path: str, old_string: str, new_string: str
) -> Dict[str, Any]:
    """
    Replace first occurrence of old_string with new_string in the file. Returns same shape as write_project_file.
    """
    if not res_path or not res_path.strip():
        return {"success": False, "path": res_path, "content": "", "message": "path is required"}
    path_norm = res_path.strip().replace("\\", "/")
    if not path_norm.startswith("res://"):
        path_norm = "res://" + path_norm.lstrip("/")
    abs_path = _safe_join(project_root_abs, path_norm)
    if not _ensure_under_root(project_root_abs, abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "path is outside project"}
    if not os.path.isfile(abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "File not found: %s" % path_norm}
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            old_content = f.read()
        if len(old_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return {"success": False, "path": path_norm, "content": "", "message": "File too large to patch"}
        if old_string not in old_content:
            return {"success": False, "path": path_norm, "content": "", "message": "old_string not found in file"}
        new_content = old_content.replace(old_string, new_string, 1)
        if len(new_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return {"success": False, "path": path_norm, "content": "", "message": "Resulting content too large"}
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "path": path_norm, "content": new_content, "message": "Patched: %s" % path_norm}
    except Exception as e:
        return {"success": False, "path": path_norm, "content": "", "message": "Failed to patch: %s" % (e,)}


def apply_project_patch_unified(
    project_root_abs: str, res_path: str, diff_text: str
) -> Dict[str, Any]:
    """
    Apply a unified diff to the file. Returns same shape as apply_project_patch.
    """
    if not res_path or not res_path.strip():
        return {"success": False, "path": res_path, "content": "", "message": "path is required"}
    path_norm = res_path.strip().replace("\\", "/")
    if not path_norm.startswith("res://"):
        path_norm = "res://" + path_norm.lstrip("/")
    abs_path = _safe_join(project_root_abs, path_norm)
    if not _ensure_under_root(project_root_abs, abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "path is outside project"}
    if not os.path.isfile(abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "File not found: %s" % path_norm}
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            old_content = f.read()
        if len(old_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return {"success": False, "path": path_norm, "content": "", "message": "File too large to patch"}
        new_content = _apply_unified_diff(old_content, diff_text)
        if new_content is None:
            return {"success": False, "path": path_norm, "content": "", "message": "Failed to apply unified diff"}
        if len(new_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return {"success": False, "path": path_norm, "content": "", "message": "Resulting content too large"}
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "path": path_norm, "content": new_content, "message": "Patched (diff): %s" % path_norm}
    except Exception as e:
        return {"success": False, "path": path_norm, "content": "", "message": "Failed to patch: %s" % (e,)}


def append_project_file(project_root_abs: str, res_path: str, content: str) -> Dict[str, Any]:
    """
    Append content to the end of a file. Creates the file if it does not exist.
    Returns same shape as write_project_file (content = full file content after append).
    """
    if not res_path or not res_path.strip():
        return {"success": False, "path": res_path, "content": "", "message": "path is required"}
    path_norm = res_path.strip().replace("\\", "/")
    if not path_norm.startswith("res://"):
        path_norm = "res://" + path_norm.lstrip("/")
    abs_path = _safe_join(project_root_abs, path_norm)
    if not _ensure_under_root(project_root_abs, abs_path):
        return {"success": False, "path": path_norm, "content": "", "message": "path is outside project"}
    try:
        old_content = ""
        if os.path.isfile(abs_path):
            with open(abs_path, "r", encoding="utf-8") as f:
                old_content = f.read()
        new_content = old_content + content
        if len(new_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return {"success": False, "path": path_norm, "content": "", "message": "Resulting content too large"}
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "path": path_norm, "content": new_content, "message": "Appended: %s" % path_norm}
    except Exception as e:
        return {"success": False, "path": path_norm, "content": "", "message": "Failed to append: %s" % (e,)}


def read_project_file(
    project_root_abs: str, res_path: str, max_bytes: int = 200_000
) -> Optional[str]:
    try:
        abs_path = _safe_join(project_root_abs, res_path)
        with open(abs_path, "rb") as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            data = data[:max_bytes] + b"\n\n[...truncated file read...]\n"
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


def list_project_files(
    project_root_abs: str,
    res_path: str = "res://",
    recursive: bool = True,
    extensions: Optional[List[str]] = None,
    max_entries: int = 500,
) -> List[str]:
    """
    List file paths under project_root_abs under res_path.
    Returns res://-prefixed paths.
    """
    if not project_root_abs or not os.path.isdir(project_root_abs):
        return []
    rp = res_path.replace("\\", "/").strip()
    if rp.startswith("res://"):
        rp = rp[len("res://"):].lstrip("/")
    root = os.path.abspath(os.path.join(project_root_abs, rp))
    if not root.startswith(os.path.abspath(project_root_abs)):
        return []
    exts = set()
    if extensions:
        for e in extensions:
            e = (e or "").strip().lower()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                exts.add(e)
    out: List[str] = []

    def walk(dir_abs: str, dir_res: str) -> None:
        if len(out) >= max_entries:
            return
        try:
            entries = os.listdir(dir_abs)
        except OSError:
            return
        for name in sorted(entries):
            if len(out) >= max_entries:
                return
            if name.startswith(".") and name == ".godot":
                continue
            child_abs = os.path.join(dir_abs, name)
            child_res = (dir_res + "/" + name) if dir_res else name
            if os.path.isdir(child_abs):
                if recursive:
                    walk(child_abs, child_res)
                continue
            if exts:
                ext = "." + (os.path.splitext(name)[1] or "").lower()
                if ext not in exts:
                    continue
            out.append("res://" + child_res.replace("\\", "/"))

    start_res = rp.replace("\\", "/") if rp else ""
    if os.path.isdir(root):
        walk(root, start_res)
    return out


def list_project_directory(
    project_root_abs: str,
    res_path: str = "res://",
    recursive: bool = False,
    max_entries: int = 250,
    max_depth: int = 6,
) -> List[Dict[str, Any]]:
    """
    List directory entries (files and dirs) under project_root_abs under res_path.
    Returns list of {"name": str, "path": str (res://), "is_dir": bool}.
    """
    if not project_root_abs or not os.path.isdir(project_root_abs):
        return []
    rp = res_path.replace("\\", "/").strip()
    if rp.startswith("res://"):
        rp = rp[len("res://"):].lstrip("/")
    root = os.path.abspath(os.path.join(project_root_abs, rp))
    if not root.startswith(os.path.abspath(project_root_abs)):
        return []
    out: List[Dict[str, Any]] = []

    def walk(dir_abs: str, dir_res: str, depth: int) -> None:
        if len(out) >= max_entries or depth > max_depth:
            return
        try:
            entries = os.listdir(dir_abs)
        except OSError:
            return
        for name in sorted(entries):
            if len(out) >= max_entries:
                return
            if name.startswith(".") and name != ".godot":
                continue
            child_abs = os.path.join(dir_abs, name)
            child_res = (dir_res + "/" + name) if dir_res else name
            is_dir = os.path.isdir(child_abs)
            out.append({
                "name": name,
                "path": "res://" + child_res.replace("\\", "/"),
                "is_dir": is_dir,
            })
            if is_dir and recursive and depth < max_depth:
                walk(child_abs, child_res, depth + 1)

    start_res = rp.replace("\\", "/") if rp else ""
    if os.path.isdir(root):
        walk(root, start_res, 0)
    return out


def search_project_files(
    project_root_abs: str,
    query: str,
    root_path: str = "res://",
    extensions: Optional[List[str]] = None,
    max_matches: int = 50,
) -> List[Dict[str, Any]]:
    """
    Grep: find files under root_path whose content contains query.
    Returns list of {"path": str (res://), "matches": list of {"line_no": int, "line": str}}.
    """
    if not project_root_abs or not os.path.isdir(project_root_abs) or not query:
        return []
    rp = root_path.replace("\\", "/").strip()
    if rp.startswith("res://"):
        rp = rp[len("res://"):].lstrip("/")
    root = os.path.abspath(os.path.join(project_root_abs, rp))
    if not root.startswith(os.path.abspath(project_root_abs)):
        return []
    exts = set()
    if extensions:
        for e in extensions:
            e = (e or "").strip().lower()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                exts.add(e)
    out: List[Dict[str, Any]] = []
    q_lower = query.lower()

    def scan_file(file_abs: str, file_res: str) -> None:
        if len(out) >= max_matches:
            return
        try:
            with open(file_abs, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return
        matches: List[Dict[str, Any]] = []
        for i, line in enumerate(lines, 1):
            if q_lower in line.lower():
                matches.append({"line_no": i, "line": line.rstrip("\n\r")})
                if len(matches) >= 10:
                    break
        if matches:
            out.append({"path": "res://" + file_res.replace("\\", "/"), "matches": matches})

    def walk(dir_abs: str, dir_res: str) -> None:
        if len(out) >= max_matches:
            return
        try:
            entries = os.listdir(dir_abs)
        except OSError:
            return
        for name in sorted(entries):
            if len(out) >= max_matches:
                return
            if name.startswith(".") and name == ".godot":
                continue
            child_abs = os.path.join(dir_abs, name)
            child_res = (dir_res + "/" + name) if dir_res else name
            if os.path.isdir(child_abs):
                walk(child_abs, child_res)
                continue
            if exts:
                ext = "." + (os.path.splitext(name)[1] or "").lower()
                if ext not in exts:
                    continue
            try:
                scan_file(child_abs, child_res)
            except Exception:
                pass

    start_res = rp.replace("\\", "/") if rp else ""
    if os.path.isdir(root):
        walk(root, start_res)
    return out


def grep_project_files(
    project_root_abs: str,
    pattern: str,
    root_path: str = "res://",
    extensions: Optional[List[str]] = None,
    max_matches: int = 100,
    use_regex: bool = True,
) -> List[Dict[str, Any]]:
    """
    Search project files for a pattern (regex or literal). Returns list of
    {"path": res://, "line_no": int, "line": str} per match.
    """
    if not project_root_abs or not os.path.isdir(project_root_abs) or not pattern:
        return []
    rp = root_path.replace("\\", "/").strip()
    if rp.startswith("res://"):
        rp = rp[len("res://"):].lstrip("/")
    root = os.path.abspath(os.path.join(project_root_abs, rp))
    if not root.startswith(os.path.abspath(project_root_abs)):
        return []
    exts = set()
    if extensions:
        for e in extensions:
            e = (e or "").strip().lower()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                exts.add(e)
    try:
        re_pat = re.compile(pattern, re.IGNORECASE) if use_regex else re.compile(re.escape(pattern), re.IGNORECASE)
    except re.error:
        re_pat = re.compile(re.escape(pattern), re.IGNORECASE)
    out: List[Dict[str, Any]] = []

    def scan_file(file_abs: str, file_res: str) -> None:
        if len(out) >= max_matches:
            return
        try:
            with open(file_abs, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return
        for i, line in enumerate(lines, 1):
            if len(out) >= max_matches:
                return
            if re_pat.search(line):
                out.append({
                    "path": "res://" + file_res.replace("\\", "/"),
                    "line_no": i,
                    "line": line.rstrip("\n\r"),
                })

    def walk(dir_abs: str, dir_res: str) -> None:
        if len(out) >= max_matches:
            return
        try:
            entries = os.listdir(dir_abs)
        except OSError:
            return
        for name in sorted(entries):
            if len(out) >= max_matches:
                return
            if name.startswith(".") and name == ".godot":
                continue
            child_abs = os.path.join(dir_abs, name)
            child_res = (dir_res + "/" + name) if dir_res else name
            if os.path.isdir(child_abs):
                walk(child_abs, child_res)
                continue
            if exts:
                ext = "." + (os.path.splitext(name)[1] or "").lower()
                if ext not in exts:
                    continue
            scan_file(child_abs, child_res)

    start_res = rp.replace("\\", "/") if rp else ""
    if os.path.isdir(root):
        walk(root, start_res)
    return out


def read_project_godot_ini(project_root_abs: str) -> Dict[str, Dict[str, str]]:
    """
    Read project.godot and return a dict of section -> { key: value }.
    Used for [autoload], [input], and other sections.
    """
    if not project_root_abs or not os.path.isdir(project_root_abs):
        return {}
    path = os.path.join(project_root_abs, "project.godot")
    if not os.path.isfile(path):
        return {}
    result: Dict[str, Dict[str, str]] = {}
    current: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(";"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1].strip()
                    if current:
                        result.setdefault(current, {})
                    continue
                if current and "=" in line:
                    k, _, v = line.partition("=")
                    key = k.strip().strip('"')
                    val = v.strip().strip('"')
                    result[current][key] = val
    except Exception:
        pass
    return result


def extract_structural_deps(text: str) -> List[str]:
    """
    Heuristic dependency extraction: res:// paths from preload/load/extends/ResourceLoader.
    """
    if not text:
        return []
    found = _RE_RES_PATH.findall(text)
    seen: set = set()
    out: List[str] = []
    for p in found:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def build_related_files_context(
    *,
    project_root_abs: str,
    active_file_res_path: str,
    active_file_text: str,
    max_files: int = 4,
) -> List[Tuple[str, str]]:
    """
    One-hop structural proximity from heuristics.

    NOTE: The plugin now provides one-hop `related_res_paths` client-side.
    This function remains as a stateless fallback (no SQLite indexing).
    Returns list of (res_path, content).
    """
    deps = extract_structural_deps(active_file_text)
    related: List[Tuple[str, str]] = []
    for p in deps:
        if len(related) >= max_files:
            break
        if p == active_file_res_path:
            continue
        content = read_project_file(project_root_abs, p)
        if content:
            related.append((p, content))
    return related
