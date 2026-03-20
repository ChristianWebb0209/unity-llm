"""
Current scene analysis: parse .tscn for script paths, load scene scripts,
extract extends/base class from script content.
"""

import re
from typing import List, Optional, Tuple

from .budget import estimate_tokens, fit_block_text
from .project import read_project_file

_RE_TSCN_SCRIPT_REF = re.compile(r'^\s*script\s*=\s*ExtResource\s*\(\s*["\']?([^"\')\s]+)["\']?\s*\)')


def parse_tscn_script_paths(tscn_text: str) -> List[str]:
    """
    Extract script paths from a .tscn file (res://... paths).
    Parses [ext_resource type="Script" path="res://..." id="N"] and script = ExtResource("N").
    """
    ext_resources: dict = {}
    current_section: Optional[str] = None
    for raw in tscn_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line
            if "ext_resource" in current_section and "Script" in current_section:
                path_m = re.search(r'path="(res://[^"]+\.(?:gd|cs))"', current_section)
                id_m = re.search(r'\bid="([^"]+)"', current_section)
                if path_m and id_m:
                    ext_resources[id_m.group(1)] = path_m.group(1)
            continue
    used: List[str] = []
    seen: set = set()
    for line in tscn_text.splitlines():
        ref = _RE_TSCN_SCRIPT_REF.match(line.strip())
        if ref:
            path = ext_resources.get(ref.group(1))
            if path and path not in seen:
                seen.add(path)
                used.append(path)
    if not used and ext_resources:
        for p in ext_resources.values():
            if p not in seen:
                seen.add(p)
                used.append(p)
    return used


def extract_extends_from_script(text: str, language: str = "gdscript") -> Optional[str]:
    """
    Extract extends/class base type from script content.
    GDScript: extends Node2D; C#: class Foo : CharacterBody2D or Godot.CharacterBody2D.
    Returns base type string (e.g. CharacterBody2D) or None.
    """
    if not text or not text.strip():
        return None
    lines = text.splitlines()
    if language == "csharp" or (language != "gdscript" and "class " in text[:2000]):
        for ln in lines[:80]:
            m = re.match(
                r"^\s*(?:public\s+|internal\s+|partial\s+)*class\s+[A-Za-z0-9_]+\s*:\s*([A-Za-z0-9_.]+)",
                ln,
            )
            if m:
                base = m.group(1).strip()
                if base.startswith("Godot."):
                    return base[6:].strip()
                return base
        return None
    for ln in lines[:25]:
        m = re.match(r'^\s*extends\s+([A-Za-z0-9_".]+)', ln)
        if m:
            base = m.group(1).strip().strip('"')
            if base.startswith("res://"):
                return None
            return base
    return None


def build_current_scene_scripts_context(
    project_root_abs: str,
    scene_res_path: str,
    *,
    max_scripts: int = 12,
    max_tokens_per_script: int = 1200,
    exclude_path: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """
    Parse the current scene .tscn, collect script paths, read each file.
    Returns (res_path, content) with content trimmed to max_tokens_per_script.
    exclude_path: e.g. active file to avoid duplicating in active_file block.
    """
    tscn_content = read_project_file(project_root_abs, scene_res_path, max_bytes=500_000)
    if not tscn_content:
        return []
    paths = parse_tscn_script_paths(tscn_content)
    if not paths:
        return []
    out: List[Tuple[str, str]] = []
    for res_path in paths[:max_scripts]:
        norm = res_path.replace("\\", "/").strip()
        if exclude_path:
            ex = exclude_path.replace("\\", "/").strip()
            if norm == ex or norm.endswith("/" + ex):
                continue
        content = read_project_file(project_root_abs, res_path, max_bytes=150_000)
        if not content or len(content.strip()) < 10:
            continue
        if estimate_tokens(content) > max_tokens_per_script:
            content, _ = fit_block_text(content, max_tokens_per_script)
        out.append((res_path, content))
    return out
