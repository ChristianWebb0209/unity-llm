from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _storage_root() -> Path:
    override = os.getenv("UNITY_LLM_LOCAL_STORAGE_ROOT", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = _service_root() / "data" / "unity_local"
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_id_for_root(project_root_abs: str) -> str:
    norm = os.path.abspath(project_root_abs).replace("\\", "/").lower()
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    return digest[:24]


def project_storage_dir(project_root_abs: str) -> Path:
    d = _storage_root() / "projects" / project_id_for_root(project_root_abs)
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def prune_old_files(directory: Path, pattern: str, keep_latest: int = 5) -> None:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for p in files[keep_latest:]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def now_ts() -> float:
    return time.time()
