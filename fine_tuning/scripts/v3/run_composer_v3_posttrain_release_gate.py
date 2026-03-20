#!/usr/bin/env python3
"""
Run Composer v3 post-train release gate.
Blocks release on inference contract failures.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "fine_tuning" / "testing" / "results"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Composer v3 post-train release gate")
    parser.add_argument("--url", type=str, default="")
    parser.add_argument("--server-timeout-seconds", type=int, default=120)
    args = parser.parse_args()

    suite_json = RESULTS_DIR / "composer_v3_inference_contract_suite.json"
    cmd = [
        sys.executable,
        "-m",
        "fine_tuning.testing.composer_v3_inference_contract_suite",
        "--output-json",
        str(suite_json),
        "--server-timeout-seconds",
        str(args.server_timeout_seconds),
    ]
    if args.url.strip():
        cmd.extend(["--url", args.url.strip()])

    # Let the suite complete and write output; interpret pass/fail here.
    proc = subprocess.run(cmd, check=False)
    if not suite_json.exists():
        raise SystemExit("Inference suite did not produce output.")
    suite = _read_json(suite_json)
    if suite.get("status") == "infra_failed":
        out = {
            "status": "skipped",
            "reason": "composer_upstream_unavailable",
            "hint": "Start rag_service and ensure COMPOSER/VASTAI base URL reaches a running LoRA server.",
            "suite_report": str(suite_json),
        }
        out_path = RESULTS_DIR / "composer_v3_posttrain_release_gate.json"
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(out, indent=2, ensure_ascii=False))
        raise SystemExit(3)

    failures = suite.get("failures") or []

    blocking_errors: List[str] = []
    for f in failures:
        for e in f.get("errors") or []:
            if str(e).startswith("unknown_tool:"):
                blocking_errors.append(str(e))
            if str(e).startswith("tool_not_in_contract:"):
                blocking_errors.append(str(e))
            if str(e) in ("tool_calls_not_list", "tool_call_not_object", "arguments_not_object"):
                blocking_errors.append(str(e))
            if str(e) in ("agent_no_tool_calls", "ask_has_tool_calls", "ask_answer_not_question"):
                blocking_errors.append(str(e))

    status = (
        "passed"
        if (not blocking_errors and suite.get("status") == "passed" and proc.returncode == 0)
        else "failed"
    )
    out = {
        "status": status,
        "blocking_error_count": len(blocking_errors),
        "blocking_errors_sample": blocking_errors[:25],
        "suite_report": str(suite_json),
    }
    out_path = RESULTS_DIR / "composer_v3_posttrain_release_gate.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
