from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .local_storage import atomic_write_json, now_ts, project_storage_dir, read_json


@dataclass(frozen=True)
class RepoIndexConfig:
    max_text_file_bytes: int = 1024 * 1024
    include_extensions: Tuple[str, ...] = (
        ".unity",
        ".prefab",
        ".asset",
        ".mat",
        ".controller",
        ".anim",
        ".cs",
        ".shader",
        ".cginc",
        ".compute",
        ".uxml",
        ".uss",
    )
    ignore_dirs: Tuple[str, ...] = (
        ".git",
        "Library",
        "Temp",
        "Logs",
        "obj",
        "bin",
        "Build",
    )


_RE_UNITY_PATH = re.compile(r"(Assets/[A-Za-z0-9_\-./]+)")


def _to_rel_from_asset(asset_or_rel: str) -> str:
    p = (asset_or_rel or "").replace("\\", "/").strip()
    if p.startswith("Assets/"):
        return p
    return p.lstrip("/")


def _to_asset_from_rel(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if rel.startswith("Assets/"):
        return rel
    return f"Assets/{rel}" if rel else "Assets"


def _index_path(project_root_abs: str) -> Path:
    return project_storage_dir(project_root_abs) / "repo_index.json"


def _safe_rel_path(project_root: Path, p: Path) -> str:
    return p.relative_to(project_root).as_posix()


def _walk_files(project_root: Path, cfg: RepoIndexConfig) -> Iterable[Path]:
    ignore = set(d.lower() for d in cfg.ignore_dirs)
    for p in project_root.rglob("*"):
        try:
            if p.is_dir():
                continue
        except OSError:
            continue
        if any(part.lower() in ignore for part in p.parts):
            continue
        if p.suffix.lower() in cfg.include_extensions:
            yield p


def _extract_asset_paths(text: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for m in _RE_UNITY_PATH.finditer(text or ""):
        ap = _to_asset_from_rel(m.group(1))
        if ap in seen:
            continue
        seen.add(ap)
        out.append(ap)
    return out


def _load_index(project_root_abs: str) -> Dict[str, Any]:
    return read_json(_index_path(project_root_abs)) or {
        "project_root_abs": os.path.abspath(project_root_abs),
        "updated_ts": 0.0,
        "files": {},
        "edges": [],
    }


def _save_index(project_root_abs: str, payload: Dict[str, Any]) -> None:
    atomic_write_json(_index_path(project_root_abs), payload)


def index_repo(
    *,
    project_root_abs: str,
    repo_id: Optional[str] = None,
    reason: str = "manual",
    config: Optional[RepoIndexConfig] = None,
) -> Dict[str, Any]:
    cfg = config or RepoIndexConfig()
    project_root = Path(project_root_abs).expanduser().resolve()

    file_count = 0
    edge_count = 0
    errors: List[str] = []
    files: Dict[str, Any] = {}
    edges: List[Dict[str, Any]] = []

    for path in _walk_files(project_root, cfg):
        rel = _safe_rel_path(project_root, path)
        asset_path = _to_asset_from_rel(rel)
        try:
            st = path.stat()
            size = int(st.st_size)
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError as e:
            errors.append(f"{asset_path}: stat failed: {e}")
            continue

        files[asset_path] = {
            "path": asset_path,
            "size_bytes": size,
            "mtime_ns": mtime_ns,
        }
        file_count += 1

        if size > cfg.max_text_file_bytes:
            continue

        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass

        for target in _extract_asset_paths(text):
            if target == asset_path:
                continue
            edges.append({"src": asset_path, "dst": target, "edge_type": "references_asset"})
            edge_count += 1

    payload = {
        "project_root_abs": str(project_root),
        "updated_ts": now_ts(),
        "reason": reason,
        "files": files,
        "edges": edges,
        "errors": errors[:100],
    }
    _save_index(str(project_root), payload)

    return {
        "ok": len(errors) == 0,
        "repo_id": repo_id or "local-json-index",
        "project_root_abs": str(project_root),
        "files_indexed": file_count,
        "edges_indexed": edge_count,
        "errors": errors[:50],
        "index_path": str(_index_path(str(project_root))),
        "elapsed_s": 0.0,
    }


def get_repo_index_stats(project_root_abs: str) -> Dict[str, Any]:
    idx = read_json(_index_path(project_root_abs))
    if not idx:
        return {"error": "not_indexed"}
    return {
        "files": len((idx.get("files") or {})),
        "edges": len((idx.get("edges") or [])),
    }


def _ensure_index(project_root_abs: str) -> Dict[str, Any]:
    idx = _load_index(project_root_abs)
    if not idx.get("files"):
        index_repo(project_root_abs=project_root_abs, reason="auto")
        idx = _load_index(project_root_abs)
    return idx


def get_related_res_paths(
    *,
    project_root_abs: str,
    active_file_res_path: str,
    max_outbound: int = 8,
    max_inbound: int = 4,
) -> List[str]:
    idx = _ensure_index(project_root_abs)
    active = _to_asset_from_rel(_to_rel_from_asset(active_file_res_path))
    edges = idx.get("edges") or []
    out: List[str] = []

    for e in edges:
        if len(out) >= max_outbound:
            break
        if e.get("src") == active:
            dst = _to_asset_from_rel(_to_rel_from_asset(str(e.get("dst") or "")))
            if dst and dst != active and dst not in out:
                out.append(dst)

    for e in edges:
        if len(out) >= (max_outbound + max_inbound):
            break
        if e.get("dst") == active:
            src = _to_asset_from_rel(_to_rel_from_asset(str(e.get("src") or "")))
            if src and src != active and src not in out:
                out.append(src)

    return out


def get_most_referenced_res_paths(
    *,
    project_root_abs: str,
    limit: int = 10,
    edge_types: Optional[Sequence[str]] = None,
) -> List[str]:
    idx = _ensure_index(project_root_abs)
    counts: Dict[str, int] = {}
    for e in idx.get("edges") or []:
        et = str(e.get("edge_type") or "")
        if edge_types and et not in edge_types:
            continue
        dst = _to_asset_from_rel(_to_rel_from_asset(str(e.get("dst") or "")))
        if not dst:
            continue
        counts[dst] = counts.get(dst, 0) + 1

    return [p for p, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def list_indexed_paths(
    project_root_abs: str,
    prefix: str = "Assets",
    max_paths: int = 500,
    max_depth: Optional[int] = None,
) -> List[str]:
    idx = _ensure_index(project_root_abs)
    files = sorted((idx.get("files") or {}).keys())
    normalized_prefix = _to_asset_from_rel(_to_rel_from_asset(prefix)).rstrip("/")
    out: List[str] = []
    for p in files:
        if normalized_prefix and not p.startswith(normalized_prefix):
            continue
        if max_depth is not None and len(p.split("/")) > max_depth:
            continue
        out.append(p)
        if len(out) >= max_paths:
            break
    return out


def get_inbound_refs(
    project_root_abs: str,
    target_res_path: str,
    limit: int = 20,
) -> List[str]:
    idx = _ensure_index(project_root_abs)
    target = _to_asset_from_rel(_to_rel_from_asset(target_res_path))
    out: List[str] = []
    for e in idx.get("edges") or []:
        if e.get("dst") == target:
            src = _to_asset_from_rel(_to_rel_from_asset(str(e.get("src") or "")))
            if src and src != target and src not in out:
                out.append(src)
                if len(out) >= limit:
                    break
    return out
