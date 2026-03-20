import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _db_root() -> str:
    # Centralized under rag_service/data/db so all SQLite files live together.
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "db")
    )


def _db_path_for_repo_id(repo_id: str) -> str:
    # Per-repo DB avoids cross-project lock contention and keeps indexes portable.
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", repo_id or "unknown")
    root = _db_root()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, f"repo_index_{safe}.db")


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    # Avoid transient "database is locked" during rapid successive requests.
    conn.execute("PRAGMA busy_timeout=3000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_repo_index_db(db_path: str) -> None:
    conn = _get_conn(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS repos (
              id TEXT PRIMARY KEY,
              root_abs TEXT NOT NULL UNIQUE,
              created_ts REAL NOT NULL,
              updated_ts REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS index_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo_id TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
              started_ts REAL NOT NULL,
              finished_ts REAL,
              status TEXT NOT NULL, -- running|ok|error
              reason TEXT NOT NULL,
              error TEXT
            );

            CREATE TABLE IF NOT EXISTS files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo_id TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
              path_rel TEXT NOT NULL,
              kind TEXT NOT NULL,        -- godot_scene|script|shader|resource|project_config|other
              language TEXT,             -- gdscript|csharp|gdshader|...
              size_bytes INTEGER NOT NULL DEFAULT 0,
              mtime_ns INTEGER,
              sha256 TEXT,
              indexed_ts REAL NOT NULL,
              UNIQUE(repo_id, path_rel)
            );

            CREATE TABLE IF NOT EXISTS edges (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo_id TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
              src_rel TEXT NOT NULL,
              dst_res TEXT NOT NULL,     -- may be a path_rel or a res:// path
              edge_type TEXT NOT NULL,   -- attaches_script|instances_scene|uses_resource|references_res_path|autoload|main_scene
              meta_json TEXT,
              UNIQUE(repo_id, src_rel, dst_res, edge_type)
            );

            CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo_id, path_rel);
            CREATE INDEX IF NOT EXISTS idx_edges_repo_src ON edges(repo_id, src_rel);
            CREATE INDEX IF NOT EXISTS idx_edges_repo_dst ON edges(repo_id, dst_res);
            CREATE INDEX IF NOT EXISTS idx_runs_repo_started ON index_runs(repo_id, started_ts DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class RepoIndexConfig:
    max_text_file_bytes: int = 1024 * 1024  # 1MB safety cap
    include_extensions: Tuple[str, ...] = (
        ".godot",
        ".tscn",
        ".tres",
        ".res",
        ".gd",
        ".cs",
        ".gdshader",
    )
    # Ignore folders that are commonly huge/derived.
    ignore_dirs: Tuple[str, ...] = (".git", ".godot", ".import", "Library", "Temp", "obj", "bin")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _safe_rel_path(project_root: Path, p: Path) -> str:
    return p.relative_to(project_root).as_posix()


def _normalize_res_path(res_path: str) -> str:
    res_path = res_path.strip().strip('"').strip("'")
    if res_path.startswith("res://"):
        return res_path
    return res_path


_RE_RES_PATH = re.compile(r'(res://[A-Za-z0-9_\-./]+)')


def _extract_res_paths(text: str) -> List[str]:
    return [_normalize_res_path(m.group(1)) for m in _RE_RES_PATH.finditer(text)]


def _file_kind_and_language(path: Path) -> Tuple[str, Optional[str]]:
    suffix = path.suffix.lower()
    if path.name == "project.godot":
        return "project_config", None
    if suffix == ".tscn":
        return "godot_scene", None
    if suffix in (".tres", ".res"):
        return "resource", None
    if suffix == ".gd":
        return "script", "gdscript"
    if suffix == ".cs":
        return "script", "csharp"
    if suffix == ".gdshader":
        return "shader", "gdshader"
    return "other", None


def _parse_project_godot_edges(text: str) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    """
    Return edges as tuples: (src_rel, dst_res, edge_type, meta).
    For project.godot, src_rel is always 'project.godot'.
    """
    edges: List[Tuple[str, str, str, Dict[str, Any]]] = []
    section: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if "=" not in line:
            continue
        key, value = [p.strip() for p in line.split("=", 1)]
        value = value.strip("\"'")
        if section == "application" and key == "run/main_scene":
            edges.append(("project.godot", _normalize_res_path(value), "main_scene", {}))
        elif section and section.startswith("autoload"):
            # MySingleton="*res://autoload/foo.gd"
            script_path = value.lstrip("*")
            edges.append(
                (
                    "project.godot",
                    _normalize_res_path(script_path),
                    "autoload",
                    {"name": key},
                )
            )
    return edges


def _parse_tscn_edges(text: str, scene_src_rel: str) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    """
    Minimal .tscn parser: capture ext_resource path ids, then convert:
    - script = ExtResource("id") -> attaches_script
    - instance=ExtResource("id") -> instances_scene (if .tscn) else uses_resource
    Also records any raw res:// strings as references_res_path.
    """
    edges: List[Tuple[str, str, str, Dict[str, Any]]] = []
    ext_id_to_path: Dict[str, str] = {}

    current_section: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            current_section = line.strip("[]")
            if current_section.startswith("ext_resource"):
                m_id = re.search(r'id="([^"]+)"', current_section)
                m_path = re.search(r'path="([^"]+)"', current_section)
                if m_id and m_path:
                    ext_id_to_path[m_id.group(1)] = _normalize_res_path(m_path.group(1))
            elif current_section.startswith("node "):
                m_inst = re.search(r'instance=ExtResource\("(\d+)"\)', current_section)
                if m_inst:
                    res_id = m_inst.group(1)
                    dst = ext_id_to_path.get(res_id)
                    if dst:
                        if dst.lower().endswith(".tscn"):
                            edges.append((scene_src_rel, dst, "instances_scene", {}))
                        else:
                            edges.append((scene_src_rel, dst, "uses_resource", {}))
            continue

        if current_section and current_section.startswith("node "):
            m_script = re.search(r'^script\s*=\s*ExtResource\("([^"]+)"\)', line)
            if m_script:
                res_id = m_script.group(1)
                dst = ext_id_to_path.get(res_id)
                if dst:
                    edges.append((scene_src_rel, dst, "attaches_script", {}))

    # Fallback: capture raw res:// occurrences too (covers cases not expressed via ExtResource).
    for res in _extract_res_paths(text):
        edges.append((scene_src_rel, res, "references_res_path", {}))
    return edges


def _parse_text_file_edges(text: str, src_rel: str) -> List[Tuple[str, str, str, Dict[str, Any]]]:
    edges: List[Tuple[str, str, str, Dict[str, Any]]] = []
    for res in _extract_res_paths(text):
        edges.append((src_rel, res, "references_res_path", {}))
    return edges


def _walk_files(project_root: Path, cfg: RepoIndexConfig) -> Iterable[Path]:
    ignore = set(d.lower() for d in cfg.ignore_dirs)
    for p in project_root.rglob("*"):
        try:
            if p.is_dir():
                # Best-effort prune: rglob doesn't support skipping directly, but we can skip work.
                continue
        except OSError:
            continue

        parts_lower = [part.lower() for part in p.parts]
        if any(part in ignore for part in parts_lower):
            continue

        if p.suffix.lower() in cfg.include_extensions or p.name == "project.godot":
            yield p


def _default_repo_id(project_root_abs: str) -> str:
    # Stable-ish id derived from canonical absolute path.
    norm = os.path.abspath(project_root_abs).replace("\\", "/").lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def index_repo(
    *,
    project_root_abs: str,
    repo_id: Optional[str] = None,
    reason: str = "manual",
    config: Optional[RepoIndexConfig] = None,
) -> Dict[str, Any]:
    """
    Build/update a lightweight SQLite repo index for a Godot project.

    Current goal: fast local queries for "what files exist" + "how are files connected"
    (scenes -> scripts/resources, scripts -> res:// refs, project.godot -> main/autoload).

    Returns a small summary dict suitable for CLI/diagnostics.
    """
    cfg = config or RepoIndexConfig()
    project_root = Path(project_root_abs).expanduser().resolve()
    rid = repo_id or _default_repo_id(str(project_root))
    db_path = _db_path_for_repo_id(rid)
    init_repo_index_db(db_path)

    started = time.time()
    conn = _get_conn(db_path)
    run_id: Optional[int] = None

    file_count = 0
    edge_count = 0
    errors: List[str] = []

    try:
        now = time.time()
        conn.execute(
            """
            INSERT INTO repos(id, root_abs, created_ts, updated_ts)
            VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET root_abs=excluded.root_abs, updated_ts=excluded.updated_ts
            """,
            (rid, str(project_root), now, now),
        )
        cur = conn.execute(
            "INSERT INTO index_runs(repo_id, started_ts, status, reason) VALUES (?,?,?,?)",
            (rid, started, "running", reason),
        )
        run_id = int(cur.lastrowid)
        conn.commit()

        # Incremental strategy:
        # - Keep existing rows for unchanged files.
        # - For changed files: upsert file row, delete prior edges for that src_rel, then reinsert.
        # - For deleted files: remove file rows + edges.
        existing_paths = conn.execute(
            "SELECT path_rel, mtime_ns, size_bytes, sha256 FROM files WHERE repo_id = ?",
            (rid,),
        ).fetchall()
        existing: Dict[str, Tuple[Optional[int], int, Optional[str]]] = {
            str(r["path_rel"]): (
                int(r["mtime_ns"]) if r["mtime_ns"] is not None else None,
                int(r["size_bytes"] or 0),
                str(r["sha256"]) if r["sha256"] else None,
            )
            for r in existing_paths
        }
        seen_paths: set[str] = set()

        for path in _walk_files(project_root, cfg):
            src_rel = _safe_rel_path(project_root, path)
            seen_paths.add(src_rel)
            kind, lang = _file_kind_and_language(path)
            try:
                st = path.stat()
                size = int(st.st_size)
                mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
            except OSError as e:
                errors.append(f"{src_rel}: stat failed: {e}")
                continue

            prev = existing.get(src_rel)
            unchanged = False
            if prev:
                prev_mtime_ns, prev_size, prev_sha = prev
                # Fast path: if mtime+size unchanged and we previously had a sha, treat as unchanged.
                # (mtime is good enough on local dev machines; sha is extra confidence.)
                if prev_mtime_ns == mtime_ns and prev_size == size and prev_sha:
                    unchanged = True

            sha256: Optional[str] = None
            text: Optional[str] = None
            # Only (re)read contents if file changed or we don't have a prior hash.
            if not unchanged and size <= cfg.max_text_file_bytes:
                try:
                    raw = path.read_bytes()
                    sha256 = _sha256_bytes(raw)
                    try:
                        text = raw.decode("utf-8", errors="ignore")
                    except Exception:
                        text = None
                except OSError as e:
                    errors.append(f"{src_rel}: read failed: {e}")
                    text = None

            # If unchanged, keep previous hash.
            if unchanged:
                sha256 = prev[2] if prev else None

            conn.execute(
                """
                INSERT INTO files(repo_id, path_rel, kind, language, size_bytes, mtime_ns, sha256, indexed_ts)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(repo_id, path_rel) DO UPDATE SET
                  kind=excluded.kind,
                  language=excluded.language,
                  size_bytes=excluded.size_bytes,
                  mtime_ns=excluded.mtime_ns,
                  sha256=excluded.sha256,
                  indexed_ts=excluded.indexed_ts
                """,
                (
                    rid,
                    src_rel,
                    kind,
                    lang,
                    size,
                    mtime_ns,
                    sha256,
                    time.time(),
                ),
            )
            file_count += 1

            if unchanged or not text:
                continue

            # Replace edges for this source file only.
            conn.execute(
                "DELETE FROM edges WHERE repo_id = ? AND src_rel = ?",
                (rid, src_rel),
            )

            # Edges.
            file_edges: Sequence[Tuple[str, str, str, Dict[str, Any]]] = []
            if path.name == "project.godot":
                file_edges = _parse_project_godot_edges(text)
            elif path.suffix.lower() == ".tscn":
                file_edges = _parse_tscn_edges(text, src_rel)
            elif path.suffix.lower() in (".gd", ".cs", ".gdshader", ".tres", ".res"):
                file_edges = _parse_text_file_edges(text, src_rel)

            for src, dst, edge_type, meta in file_edges:
                meta_json = json.dumps(meta) if meta else None
                try:
                    conn.execute(
                        """
                        INSERT INTO edges(repo_id, src_rel, dst_res, edge_type, meta_json)
                        VALUES (?,?,?,?,?)
                        ON CONFLICT(repo_id, src_rel, dst_res, edge_type) DO UPDATE SET
                          meta_json=excluded.meta_json
                        """,
                        (rid, src, dst, edge_type, meta_json),
                    )
                    edge_count += 1
                except sqlite3.Error as e:
                    errors.append(f"{src_rel}: edge insert failed ({edge_type} -> {dst}): {e}")

        # Remove deleted files and their edges.
        deleted = [p for p in existing.keys() if p not in seen_paths]
        for rel in deleted:
            conn.execute("DELETE FROM edges WHERE repo_id = ? AND src_rel = ?", (rid, rel))
            conn.execute("DELETE FROM files WHERE repo_id = ? AND path_rel = ?", (rid, rel))

        conn.execute(
            "UPDATE index_runs SET finished_ts=?, status=?, error=? WHERE id=?",
            (time.time(), "ok" if not errors else "error", "\n".join(errors) or None, int(run_id)),
        )
        conn.execute("UPDATE repos SET updated_ts=? WHERE id=?", (time.time(), rid))
        conn.commit()
    except Exception as e:
        if run_id is not None:
            conn.execute(
                "UPDATE index_runs SET finished_ts=?, status=?, error=? WHERE id=?",
                (time.time(), "error", str(e), int(run_id)),
            )
            conn.commit()
        raise
    finally:
        conn.close()

    return {
        "ok": len(errors) == 0,
        "repo_id": rid,
        "project_root_abs": str(project_root),
        "files_indexed": file_count,
        "edges_indexed": edge_count,
        "errors": errors[:50],
        "db_path": db_path,
        "elapsed_s": max(0.0, time.time() - started),
    }


def _to_rel_from_res(res_or_rel: str) -> str:
    rp = (res_or_rel or "").replace("\\", "/").strip()
    if rp.startswith("res://"):
        rp = rp[len("res://") :]
    return rp.lstrip("/")


def _to_res_from_rel(rel: str) -> str:
    rel = (rel or "").lstrip("/").replace("\\", "/")
    return f"res://{rel}"


def get_repo_index_stats(project_root_abs: str) -> Dict[str, Any]:
    """
    Return file and edge counts for the repo index, if it exists.
    Does not run indexing. Returns {"files": N, "edges": N} or {"error": "..."}.
    """
    try:
        rid = _default_repo_id(str(Path(project_root_abs).expanduser().resolve()))
        db_path = _db_path_for_repo_id(rid)
        if not os.path.isfile(db_path):
            return {"error": "not_indexed"}
        conn = _get_conn(db_path)
        try:
            files_row = conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE repo_id = ?", (rid,)
            ).fetchone()
            edges_row = conn.execute(
                "SELECT COUNT(*) AS n FROM edges WHERE repo_id = ?", (rid,)
            ).fetchone()
            return {
                "files": int(files_row["n"]) if files_row else 0,
                "edges": int(edges_row["n"]) if edges_row else 0,
            }
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


def get_related_res_paths(
    *,
    project_root_abs: str,
    active_file_res_path: str,
    max_outbound: int = 8,
    max_inbound: int = 4,
) -> List[str]:
    """
    Use the SQLite repo graph to find structurally-related files.

    Returns a list of `res://...` paths (existing files only), with a bias for:
    - outbound deps (what this file references/loads/instances)
    - inbound refs (what references this file), smaller budget
    """
    rid = _default_repo_id(str(Path(project_root_abs).expanduser().resolve()))
    db_path = _db_path_for_repo_id(rid)
    init_repo_index_db(db_path)
    rel = _to_rel_from_res(active_file_res_path)
    active_res = _to_res_from_rel(rel)

    conn = _get_conn(db_path)
    try:
        # Ensure the repo exists; if not, index it once.
        row = conn.execute("SELECT id FROM repos WHERE id = ?", (rid,)).fetchone()
        if not row:
            conn.close()
            index_repo(project_root_abs=project_root_abs, repo_id=rid, reason="context_builder")
            conn = _get_conn(db_path)

        # Collect candidate dst paths.
        outbound_rows = conn.execute(
            """
            SELECT dst_res, edge_type
            FROM edges
            WHERE repo_id = ? AND src_rel = ?
            ORDER BY
              CASE edge_type
                WHEN 'attaches_script' THEN 0
                WHEN 'instances_scene' THEN 1
                WHEN 'uses_resource' THEN 2
                WHEN 'main_scene' THEN 3
                WHEN 'autoload' THEN 4
                ELSE 10
              END ASC
            LIMIT ?
            """,
            (rid, rel, int(max_outbound)),
        ).fetchall()

        inbound_rows = conn.execute(
            """
            SELECT src_rel, edge_type
            FROM edges
            WHERE repo_id = ?
              AND (dst_res = ? OR dst_res = ?)
              AND src_rel != ?
            ORDER BY
              CASE edge_type
                WHEN 'main_scene' THEN 0
                WHEN 'autoload' THEN 1
                WHEN 'instances_scene' THEN 2
                WHEN 'attaches_script' THEN 3
                ELSE 10
              END ASC
            LIMIT ?
            """,
            (rid, active_res, rel, rel, int(max_inbound)),
        ).fetchall()

        candidates: List[str] = []
        # Outbound: dst_res already res:// for most edges.
        for r in outbound_rows:
            dst = str(r["dst_res"] or "")
            if dst:
                candidates.append(dst)
        # Inbound: src_rel is a rel-path in our db.
        for r in inbound_rows:
            src = str(r["src_rel"] or "")
            if src:
                candidates.append(_to_res_from_rel(src))

        # Filter to existing files only and dedupe, preserving order.
        seen: set[str] = set()
        out: List[str] = []
        for c in candidates:
            c_rel = _to_rel_from_res(c)
            if not c_rel:
                continue
            if c_rel == rel:
                continue
            c_res = _to_res_from_rel(c_rel)
            if c_res in seen:
                continue
            exists = conn.execute(
                "SELECT 1 FROM files WHERE repo_id = ? AND path_rel = ? LIMIT 1",
                (rid, c_rel),
            ).fetchone()
            if not exists:
                continue
            seen.add(c_res)
            out.append(c_res)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_most_referenced_res_paths(
    *,
    project_root_abs: str,
    limit: int = 10,
    edge_types: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    Return res:// paths that are referenced the most often (by inbound edge count).
    Use to find "project core" files (e.g. Player scene/script used in many scenes).

    edge_types: if set, only count these edge_type values (e.g. ["instances_scene", "attaches_script"]).
    """
    rid = _default_repo_id(str(Path(project_root_abs).expanduser().resolve()))
    db_path = _db_path_for_repo_id(rid)
    init_repo_index_db(db_path)

    conn = _get_conn(db_path)
    try:
        row = conn.execute("SELECT id FROM repos WHERE id = ?", (rid,)).fetchone()
        if not row:
            conn.close()
            index_repo(project_root_abs=project_root_abs, repo_id=rid, reason="most_referenced")
            conn = _get_conn(db_path)

        # Count inbound edges per dst; normalize dst_res to rel for grouping.
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            rows = conn.execute(
                f"""
                SELECT dst_res, COUNT(*) AS cnt
                FROM edges
                WHERE repo_id = ? AND edge_type IN ({placeholders})
                GROUP BY dst_res
                """,
                (rid, *edge_types),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT dst_res, COUNT(*) AS cnt
                FROM edges
                WHERE repo_id = ?
                GROUP BY dst_res
                """,
                (rid,),
            ).fetchall()

        # Normalize to rel path and merge counts (dst_res may be res:// or relative).
        rel_counts: Dict[str, int] = {}
        for r in rows:
            dst = str(r["dst_res"] or "").strip()
            if not dst:
                continue
            c_rel = _to_rel_from_res(dst)
            if not c_rel:
                continue
            rel_counts[c_rel] = rel_counts.get(c_rel, 0) + int(r["cnt"])

        # Sort by count descending, then filter to existing files.
        sorted_rels = sorted(rel_counts.keys(), key=lambda x: -rel_counts[x])[: limit * 2]
        out: List[str] = []
        for c_rel in sorted_rels:
            if len(out) >= limit:
                break
            exists = conn.execute(
                "SELECT 1 FROM files WHERE repo_id = ? AND path_rel = ? LIMIT 1",
                (rid, c_rel),
            ).fetchone()
            if not exists:
                continue
            out.append(_to_res_from_rel(c_rel))
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_indexed_paths(
    project_root_abs: str,
    prefix: str = "res://",
    max_paths: int = 500,
    max_depth: Optional[int] = None,
) -> List[str]:
    """
    Return res:// paths of files in the repo index under the given prefix.
    max_depth: if set, limit path segments (e.g. 3 => res://a/b/c only).
    """
    try:
        rid = _default_repo_id(str(Path(project_root_abs).expanduser().resolve()))
        db_path = _db_path_for_repo_id(rid)
        init_repo_index_db(db_path)
        rel_prefix = _to_rel_from_res(prefix)
        if not rel_prefix:
            rel_prefix = ""
        rel_prefix = rel_prefix.rstrip("/")
        if rel_prefix:
            rel_prefix = rel_prefix + "/"

        conn = _get_conn(db_path)
        try:
            row = conn.execute("SELECT id FROM repos WHERE id = ?", (rid,)).fetchone()
            if not row:
                conn.close()
                index_repo(project_root_abs=project_root_abs, repo_id=rid, reason="list_paths")
                conn = _get_conn(db_path)

            if rel_prefix:
                rows = conn.execute(
                    """
                    SELECT path_rel FROM files
                    WHERE repo_id = ? AND path_rel LIKE ?
                    ORDER BY path_rel
                    LIMIT ?
                    """,
                    (rid, rel_prefix + "%", max_paths),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT path_rel FROM files
                    WHERE repo_id = ?
                    ORDER BY path_rel
                    LIMIT ?
                    """,
                    (rid, max_paths),
                ).fetchall()

            out: List[str] = []
            for r in rows:
                path_rel = str(r["path_rel"] or "").strip()
                if not path_rel:
                    continue
                if max_depth is not None:
                    segs = path_rel.split("/")
                    if len(segs) > max_depth:
                        continue
                out.append(_to_res_from_rel(path_rel))
            return out
        finally:
            conn.close()
    except Exception:
        return []


def get_inbound_refs(
    project_root_abs: str,
    target_res_path: str,
    limit: int = 20,
) -> List[str]:
    """
    Return res:// paths of files that reference target_res_path (inbound edges).
    """
    try:
        rid = _default_repo_id(str(Path(project_root_abs).expanduser().resolve()))
        db_path = _db_path_for_repo_id(rid)
        init_repo_index_db(db_path)
        target_rel = _to_rel_from_res(target_res_path)
        target_res = _to_res_from_rel(target_rel)

        conn = _get_conn(db_path)
        try:
            row = conn.execute("SELECT id FROM repos WHERE id = ?", (rid,)).fetchone()
            if not row:
                conn.close()
                index_repo(project_root_abs=project_root_abs, repo_id=rid, reason="inbound_refs")
                conn = _get_conn(db_path)

            inbound = conn.execute(
                """
                SELECT src_rel FROM edges
                WHERE repo_id = ? AND (dst_res = ? OR dst_res = ?) AND src_rel != ?
                LIMIT ?
                """,
                (rid, target_res, target_rel, target_rel, limit),
            ).fetchall()

            out: List[str] = []
            for r in inbound:
                src = str(r["src_rel"] or "").strip()
                if src:
                    out.append(_to_res_from_rel(src))
            return out
        finally:
            conn.close()
    except Exception:
        return []

