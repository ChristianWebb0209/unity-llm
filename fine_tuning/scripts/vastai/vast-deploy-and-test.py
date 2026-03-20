#!/usr/bin/env python3
"""
Automate Vast.ai deployment of the LoRA inference server (serve_lora.py).

This script is intentionally "headless" and CLI-only:
- Assumes you're already logged in to Vast.ai (as per your PRD).
- Creates a GPU instance (3090 Ti) with 40GB disk.
- Uploads (via `vast push`) your:
  1) base model snapshot
  2) latest adapter snapshot from `fine_tuning/models/`
  3) `serve_lora.py` from this folder
  into a fixed `/workspace` layout on the remote instance.
- Starts `serve_lora.py` on port 8000.
- Creates a Vast tunnel for port 8000, discovers the public URL, and updates the
  repo root `.env` `VASTAI_BASE_URL` to `{tunnel_public_root}/v1`.
- Starts local `rag_service` and waits for it to be healthy.
- Waits for the remote LoRA server to be healthy too.
- Runs a local Composer contract test suite.

Notes / assumptions:
- Vast CLI commands vary slightly across versions. This script implements a
  "best effort" approach:
  - It tries JSON parsing when possible.
  - It falls back to regex parsing of tunnel list output when JSON isn't available.
- This script does NOT attempt to SSH into the instance.

Usage (from repo root or anywhere):
  python fine_tuning/scripts/vastai/future_vastai_lora_autodeploy.py

Required:
- Vast.ai CLI installed and authenticated (`vast login` done already).
- Local folders:
  - Base model at: C:\\Github\\base_models\\Qwen--Qwen2.5-Coder-7B-Instruct
  - Adapter directories at: fine_tuning/models/<something_with_number>
    (script picks the "most recent" by highest numeric suffix it can parse)
- Local env:
  - Root `.env` must exist; script will update `VASTAI_BASE_URL` in it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
VASTAI_DIR = Path(__file__).resolve().parent

# PRD-fixed base model absolute path.
BASE_MODEL_LOCAL_ABS = Path(r"C:\Github\base_models\Qwen--Qwen2.5-Coder-7B-Instruct")

ADAPTERS_ROOT = REPO_ROOT / "fine_tuning" / "models"

SERVE_LORA_LOCAL = VASTAI_DIR / "serve_lora.py"

RAG_SERVICE_RUN_BACKEND_PS1 = REPO_ROOT / "rag_service" / "run_backend.ps1"

ENV_FILE = REPO_ROOT / ".env"
RAG_SERVICE_LOCAL_LOG = VASTAI_DIR / "rag_service_local.log"

# Remote layout (must match serve_lora env expectations).
REMOTE_WORKSPACE = "/workspace"
REMOTE_BASE_MODEL_DIR = f"{REMOTE_WORKSPACE}/base_model"
REMOTE_ADAPTERS_DIR = f"{REMOTE_WORKSPACE}/adapters"
REMOTE_SERVE_LORA = f"{REMOTE_WORKSPACE}/serve_lora.py"

# Network configuration for serve_lora.
REMOTE_HOST = "0.0.0.0"
REMOTE_PORT = 8000


@dataclass(frozen=True)
class VastInstance:
    instance_id: str
    raw_create_output: str


def _run(cmd: List[str], *, capture: bool = True, check: bool = False, text: bool = True) -> subprocess.CompletedProcess[str]:
    """
    Run a command and return CompletedProcess.

    We always print the command so logs are debuggable without extra tooling.
    """
    print("+", " ".join(cmd))
    return subprocess.run(cmd, capture_output=capture, check=check, text=text)


def _run_capture_text(cmd: List[str]) -> str:
    p = _run(cmd, capture=True)
    if p.stdout:
        return p.stdout
    return p.stderr or ""


def _run_capture_json_if_possible(cmd: List[str]) -> Optional[Any]:
    """
    Run command and attempt to parse JSON from stdout.
    Returns parsed JSON or None if parsing fails.
    """
    p = _run(cmd, capture=True, check=False)
    raw = (p.stdout or "").strip()
    if not raw:
        raw = (p.stderr or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _discover_latest_adapter_dir() -> Path:
    """
    Pick the "most recent adapter directory" inside `fine_tuning/models/`.

    Strategy:
    - Prefer directories whose name contains an integer; choose the highest integer.
    - If no integers exist, fall back to most recent modification time.
    """
    if not ADAPTERS_ROOT.exists():
        raise SystemExit(f"Adapters root not found: {ADAPTERS_ROOT}")
    if not ADAPTERS_ROOT.is_dir():
        raise SystemExit(f"Adapters root is not a directory: {ADAPTERS_ROOT}")

    dirs = [p for p in ADAPTERS_ROOT.iterdir() if p.is_dir()]
    if not dirs:
        raise SystemExit(
            f"No adapter directories found under {ADAPTERS_ROOT}. "
            "Expected adapter snapshots to be placed there before deploying."
        )

    def score(d: Path) -> Tuple[int, float, str]:
        # Extract all integers from the name; use the last one as "adapter number".
        nums = re.findall(r"\d+", d.name)
        if nums:
            return (int(nums[-1]), d.stat().st_mtime, d.name)
        # No numeric hint: sort after numeric adapters by using -1.
        return (-1, d.stat().st_mtime, d.name)

    dirs_sorted = sorted(dirs, key=score, reverse=True)
    best = dirs_sorted[0]
    print(f"Selected latest adapter dir: {best}")
    return best


def _create_vast_instance(*, instance_name: str) -> VastInstance:
    """
    Create a Vast.ai instance headlessly.

    We request:
    - template: pytorch
    - GPU: 3090 Ti
    - disk: 40GB
    """
    cmd = [
        "vast",
        "create",
        "--template",
        "pytorch",
        "--gpu",
        "3090 ti",
        "--disk",
        "40",
        "--name",
        instance_name,
        "--json",
    ]

    # Try JSON mode first (best effort). If it isn't supported, we fall back.
    raw_json = _run_capture_json_if_possible(cmd)
    if isinstance(raw_json, dict) and raw_json.get("id"):
        return VastInstance(instance_id=str(raw_json["id"]), raw_create_output=json.dumps(raw_json))

    raw_text = _run_capture_text(cmd)
    m = re.search(r"\b(i-[a-zA-Z0-9_-]+)\b", raw_text)
    if not m:
        m = re.search(r"\b(id|instance_id)\b[^0-9]*(i-[a-zA-Z0-9_-]+|\w+-\w+)", raw_text)
    if m:
        # If the regex found group 1 is the instance id, take that. Otherwise, take last group.
        iid = m.group(1) if m.lastindex else m.group(0)
        iid = iid if isinstance(iid, str) else str(iid)
        return VastInstance(instance_id=iid, raw_create_output=raw_text)

    raise SystemExit("Failed to parse Vast instance id from `vast create` output.")


def _vast_push(instance_id: str, local_path: Path, remote_path: str) -> None:
    """
    Upload local path to remote path.
    Vast push syntax:
      vast push <instance_id> <local_path> <remote_path>
    """
    if not local_path.exists():
        raise SystemExit(f"Local path not found: {local_path}")
    cmd = ["vast", "push", instance_id, str(local_path), remote_path]
    _run(cmd, capture=True, check=True)


def _vast_run(instance_id: str, remote_cmd: str) -> None:
    """
    Run a command on the instance.
    """
    cmd = ["vast", "run", instance_id, remote_cmd]
    _run(cmd, capture=True, check=True)


def _create_tunnel_and_wait_for_url(instance_id: str, port: int, *, timeout_seconds: int = 420) -> str:
    """
    Create a Vast tunnel for `port` and return the tunnel public root URL.

    We set VASTAI_BASE_URL in the .env to {public_root}/v1, so this function
    returns the "public_root" without /v1.
    """
    # IMPORTANT:
    # Vast tunnel management is a *local* CLI operation (you run `vast tunnel ...`
    # on your machine). Do NOT attempt to run it via `vast run` on the instance.
    _run(["vast", "tunnel", "create", instance_id, str(port)], capture=True, check=False)

    deadline = time.time() + timeout_seconds
    last_raw = ""

    # Exponential backoff to handle slow Vast tunnel provisioning on new instances.
    attempt = 0
    while time.time() < deadline:
        # Prefer JSON output if supported.
        raw = _run_capture_text(["vast", "tunnel", "list"])
        last_raw = raw
        url = _extract_tunnel_url_from_text(raw, instance_id=instance_id, port=port)
        if url:
            print(f"Discovered tunnel public root URL: {url}")
            return url

        # Exponential backoff, capped.
        attempt += 1
        backoff = min(12.0, 1.8 * (1.35 ** attempt))
        print(f"Waiting for tunnel URL... attempt={attempt} backoff={backoff:.1f}s")
        time.sleep(backoff)

    raise SystemExit(f"Timed out waiting for tunnel URL. Last output:\n{last_raw}")


def _extract_tunnel_url_from_text(raw: str, *, instance_id: str, port: int) -> Optional[str]:
    """
    Extract the first HTTPS URL that likely corresponds to our instance/port.
    """
    if not raw.strip():
        return None

    # Look for lines mentioning our instance id and port.
    pattern_line = re.compile(
        rf"(?im)^(.*?{re.escape(instance_id)}.*?{port}.*)$",
        flags=0,
    )
    candidate_lines: List[str] = []
    for m in pattern_line.finditer(raw):
        candidate_lines.append(m.group(1))
    search_scope = "\n".join(candidate_lines) if candidate_lines else raw

    # Extract URLs.
    urls = re.findall(r"(https?://[^\s\"']+)", search_scope)
    if not urls:
        return None

    # Prefer URL that doesn't already include /v1.
    for u in urls:
        if not re.search(r"/v1/?$", u):
            # Strip trailing slash for consistency.
            return u.rstrip("/")
    return urls[0].rstrip("/")


def _update_env_vastai_base_url(*, public_root_url: str) -> None:
    """
    Update root `.env`'s VASTAI_BASE_URL.
    """
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing .env file at {ENV_FILE}")

    base = public_root_url.rstrip("/")
    # Ensure no double /v1.
    base = re.sub(r"/v1/?$", "", base, flags=re.IGNORECASE)
    vastai_base_url = base + "/v1"

    # Read/modify line-by-line for minimal diffs.
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    out_lines: List[str] = []
    replaced = False
    key = "VASTAI_BASE_URL"
    for ln in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", ln):
            out_lines.append(f"{key}={vastai_base_url}")
            replaced = True
        else:
            out_lines.append(ln)

    if not replaced:
        out_lines.append(f"{key}={vastai_base_url}")

    ENV_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Updated {ENV_FILE}: {key}={vastai_base_url}")


def _wait_http_ok(url: str, *, timeout_seconds: int = 180, interval_seconds: float = 2.0) -> None:
    """
    Wait until the given URL responds with HTTP status < 500.

    This uses stdlib urllib (so no extra dependencies).
    """
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout_seconds
    last_err: Optional[str] = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status and int(status) < 500:
                    print(f"HTTP ready: {url} (status={status})")
                    return
        except urllib.error.HTTPError as e:
            # Some servers respond with 302 redirects; treat 3xx as ready.
            if e.code and int(e.code) < 500:
                print(f"HTTP ready (redirect/error ok): {url} (status={e.code})")
                return
            last_err = str(e)
        except Exception as e:
            last_err = str(e)

        time.sleep(interval_seconds)

    raise SystemExit(f"Timed out waiting for HTTP readiness: {url}. Last error: {last_err}")


def _start_local_rag_service() -> subprocess.Popen[str]:
    """
    Start local rag_service backend.

    We start it in the background and rely on health polling.
    """
    if not RAG_SERVICE_RUN_BACKEND_PS1.exists():
        raise SystemExit(f"Missing run_backend.ps1: {RAG_SERVICE_RUN_BACKEND_PS1}")

    # Note: run_backend.ps1 itself selects a venv (rag_service/.venv or root .venv).
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RAG_SERVICE_RUN_BACKEND_PS1),
    ]
    log_path = RAG_SERVICE_LOCAL_LOG
    print(f"Starting local rag_service backend... (logging to {log_path})")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    # Capture stdout/stderr so we can surface startup errors if polling times out.
    return subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT, text=True)


def _tail_file(path: Path, max_lines: int = 120) -> str:
    """
    Best-effort tail for diagnostics without external deps.
    """
    if not path.exists():
        return "(log file missing)"
    try:
        # Read at most last N lines using a chunked reverse seek.
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines: List[str] = []
            # Simple fallback: iterate from end by chunks.
            # This avoids loading huge files into memory.
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size == 0:
                return "(log file empty)"
            chunk = 8192
            pos = file_size
            while pos > 0 and len(lines) < max_lines + 5:
                pos = max(0, pos - chunk)
                f.seek(pos)
                data = f.read(file_size - pos)
                lines = (data.splitlines() + lines)[-max_lines:]
                if pos == 0:
                    break
            return "\n".join(lines[-max_lines:])
    except Exception as e:
        return f"(failed to tail log: {e})"


def _tail_remote_file_best_effort(instance_id: str, remote_path: str, *, max_lines: int = 120) -> str:
    """
    Best-effort tail of a remote file using Vast CLI.

    This is only used for diagnostics when a deployment step times out.
    """
    # Try tail; if missing, fall back to cat head.
    remote_cmd = f"tail -n {max_lines} {remote_path} 2>/dev/null || (test -f {remote_path} && cat {remote_path} | tail -n {max_lines}) || true"
    try:
        raw = _run_capture_text(["vast", "run", instance_id, remote_cmd])
        s = (raw or "").strip()
        return s if s else "(remote log empty or missing)"
    except Exception as e:
        return f"(failed to tail remote log: {e})"


def _pick_venv_python(fine_tuning_dir: Path) -> str:
    """
    Prefer fine_tuning/.venv/Scripts/python.exe on Windows.
    Falls back to `python` on PATH.
    """
    candidates = [
        fine_tuning_dir / ".venv" / "Scripts" / "python.exe",
        fine_tuning_dir / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "python"


def main() -> None:
    instance_name = "unity-llm-lora-3090ti"

    for p in [BASE_MODEL_LOCAL_ABS, SERVE_LORA_LOCAL]:
        if not p.exists():
            raise SystemExit(f"Missing required local path: {p}")

    adapter_dir = _discover_latest_adapter_dir()

    # 1) Create Vast instance.
    inst = _create_vast_instance(instance_name=instance_name)
    print(f"Created Vast instance: {inst.instance_id}")

    # 2) Push base model + adapter + server script.
    # These are large transfers; a slight delay before pushing can help.
    time.sleep(4)
    _vast_push(inst.instance_id, BASE_MODEL_LOCAL_ABS, REMOTE_BASE_MODEL_DIR)
    _vast_push(inst.instance_id, adapter_dir, REMOTE_ADAPTERS_DIR)
    _vast_push(inst.instance_id, SERVE_LORA_LOCAL, REMOTE_SERVE_LORA)

    # 3) Start the remote LoRA server headlessly.
    # serve_lora.py already binds to 0.0.0.0 and defaults PORT=8000, but we set explicitly.
    # Remote command runs in a non-interactive shell; use bash -lc to ensure:
    # - `export VAR=...` syntax works (bash)
    # - environment variables apply to the server process
    # Also ensure /workspace exists before starting, and fall back if nohup is missing.
    remote_start_cmd = (
        "bash -lc "
        "\"mkdir -p " + REMOTE_WORKSPACE + " " 
        "&& "
        "export HOST=" + REMOTE_HOST + " "
        "&& "
        "export PORT=" + str(REMOTE_PORT) + " "
        "&& "
        "export ADAPTER_DIR=" + REMOTE_ADAPTERS_DIR + " "
        "&& "
        "export BASE_MODEL_LOCAL_DIR=" + REMOTE_BASE_MODEL_DIR + " "
        "&& "
        "(if command -v nohup >/dev/null 2>&1; then "
        "nohup python3 " + REMOTE_SERVE_LORA + " > " + REMOTE_WORKSPACE + "/serve_lora.log 2>&1 & "
        "else "
        "python3 " + REMOTE_SERVE_LORA + " > " + REMOTE_WORKSPACE + "/serve_lora.log 2>&1 & "
        "fi)\""
    )
    print("Starting remote serve_lora.py...")
    _vast_run(inst.instance_id, remote_start_cmd)

    # 4) Create tunnel and update root .env.
    public_root_url = _create_tunnel_and_wait_for_url(inst.instance_id, port=REMOTE_PORT)
    _update_env_vastai_base_url(public_root_url=public_root_url)

    # 5) Start local rag_service backend and wait for health.
    rag_proc = _start_local_rag_service()
    try:
        try:
            _wait_http_ok("http://127.0.0.1:8001/health", timeout_seconds=240)
        except SystemExit as e:
            print("Local rag_service failed health check.")
            rc = rag_proc.poll()
            print(f"rag_service process status: poll()={rc}")
            print("Local rag_service log tail:")
            print(_tail_file(RAG_SERVICE_LOCAL_LOG, max_lines=120))
            raise

        # Wait for remote server too (we use /health on the tunnel root).
        try:
            _wait_http_ok(public_root_url + "/health", timeout_seconds=300)
        except SystemExit:
            print("Remote serve_lora failed health check via tunnel.")
            print("Remote serve_lora log tail:")
            print(_tail_remote_file_best_effort(inst.instance_id, f"{REMOTE_WORKSPACE}/serve_lora.log", max_lines=160))
            raise

        # 6) Run local fine_tuning test suite.
        # PRD says: cd into fine_tuning, start venv, and run the tests.
        # We'll run the contract suite used by the release gate.
        fine_tuning_dir = REPO_ROOT / "fine_tuning"
        python = _pick_venv_python(fine_tuning_dir)
        test_cmd = [
            python,
            "-m",
            "testing.composer_v3_inference_contract_suite",
            "--server-timeout-seconds",
            "180",
        ]
        print("Running fine_tuning contract tests...")
        p = subprocess.run(test_cmd, cwd=str(fine_tuning_dir), check=False)
        if p.returncode != 0:
            print(f"Tests completed with non-zero exit code: {p.returncode}")
            # Keep non-zero exit as a failure signal.
            raise SystemExit(p.returncode)
    finally:
        # We deliberately do not kill rag_service automatically; leaving it running can
        # make debugging easier. If you want strict cleanup, add a terminate() here.
        pass


if __name__ == "__main__":
    main()

