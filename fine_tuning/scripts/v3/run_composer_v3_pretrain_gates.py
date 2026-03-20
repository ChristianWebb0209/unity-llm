#!/usr/bin/env python3
"""
Pre-train gate runner for Composer v3 datasets.
Fails (exit code 2) when contract thresholds are violated.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "fine_tuning" / "testing" / "results"
V3_SCRIPTS = REPO_ROOT / "fine_tuning" / "scripts" / "v3"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Composer v3 dataset pre-train gates")
    parser.add_argument("--min-agent-records", type=int, default=3000)
    parser.add_argument("--min-ask-records", type=int, default=500)
    args = parser.parse_args()

    validate_agent_json = RESULTS_DIR / "composer_v3_validate_agent.json"
    validate_ask_json = RESULTS_DIR / "composer_v3_validate_ask.json"
    audit_json = RESULTS_DIR / "composer_v3_data_audit.json"

    _run(
        [
            sys.executable,
            str(V3_SCRIPTS / "validate_composer_v3_dataset.py"),
            "--mode",
            "agent",
            "--agent-input",
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "agent_strict.jsonl"),
            "--output-json",
            str(validate_agent_json),
        ]
    )
    _run(
        [
            sys.executable,
            str(V3_SCRIPTS / "validate_composer_v3_dataset.py"),
            "--mode",
            "ask",
            "--ask-input",
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_strict.jsonl"),
            "--output-json",
            str(validate_ask_json),
        ]
    )
    _run(
        [
            sys.executable,
            str(V3_SCRIPTS / "audit_composer_v3_dataset.py"),
            "--agent-train",
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "train.jsonl"),
            "--agent-base",
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "agent_strict.jsonl"),
            "--ask-base",
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_strict.jsonl"),
            "--output-json",
            str(audit_json),
            "--output-md",
            str(RESULTS_DIR / "composer_v3_data_audit.md"),
        ]
    )

    agent_val = _read_json(validate_agent_json)
    ask_val = _read_json(validate_ask_json)
    audit = _read_json(audit_json)

    failures: list[str] = []

    agent_result = (agent_val.get("results") or {}).get("agent") or {}
    ask_result = (ask_val.get("results") or {}).get("ask") or {}
    agent_audit = audit.get("agent_base") or {}
    ask_audit = audit.get("ask_base") or {}

    if int(agent_result.get("invalid_records", 0)) != 0:
        failures.append("agent validator found invalid records")
    if int(ask_result.get("invalid_records", 0)) != 0:
        failures.append("ask validator found invalid records")
    if int(agent_audit.get("unknown_schema_tool_calls", 0)) != 0:
        failures.append("unknown schema tools present in agent set")
    if int(agent_audit.get("unknown_contract_tool_calls", 0)) != 0:
        failures.append("unknown contract tools present in agent set")
    if int(agent_audit.get("alias_tool_calls", 0)) != 0:
        failures.append("alias tools present in agent set")
    if int(agent_audit.get("malformed_tool_call_blocks", 0)) != 0:
        failures.append("malformed tool blocks present in agent set")
    if int(ask_audit.get("ask_contract_violations", 0)) != 0:
        failures.append("ask contract violations present")
    if int(agent_result.get("valid_records", 0)) < args.min_agent_records:
        failures.append(f"agent records below minimum ({args.min_agent_records})")
    if int(ask_result.get("valid_records", 0)) < args.min_ask_records:
        failures.append(f"ask records below minimum ({args.min_ask_records})")

    summary = {
        "status": "failed" if failures else "passed",
        "failures": failures,
        "artifacts": {
            "validate_agent_json": str(validate_agent_json),
            "validate_ask_json": str(validate_ask_json),
            "audit_json": str(audit_json),
            "audit_md": str(RESULTS_DIR / "composer_v3_data_audit.md"),
        },
    }
    out_path = RESULTS_DIR / "composer_v3_pretrain_gates.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
