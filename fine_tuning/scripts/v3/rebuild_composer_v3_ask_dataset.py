#!/usr/bin/env python3
"""
Rebuild strict Composer v3 ASK dataset.

Accepts only records where assistant is exactly one short question:
- no <tool_call> blocks
- no __OPTIONS__
- single line
- exactly one '?'
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from rag_service.app.prompts import COMPOSER_V2_SYSTEM_PROMPT_ASK


def _load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except Exception:
                continue


def _extract_user_assistant(record: Dict[str, Any]) -> Tuple[str, str]:
    user = ""
    assistant = ""
    msgs = record.get("messages") or []
    if not isinstance(msgs, list):
        return user, assistant
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "user":
            user = content
        elif role == "assistant":
            assistant = content
    return user, assistant


def _validate_ask(assistant: str) -> str | None:
    s = (assistant or "").strip()
    if not s:
        return "empty_assistant"
    if "<tool_call>" in s or "</tool_call>" in s:
        return "contains_tool_call"
    if "__OPTIONS__" in s:
        return "contains___OPTIONS__"
    if "\n" in s:
        return "contains_newline"
    if not s.endswith("?"):
        return "missing_question_mark"
    if s.count("?") != 1:
        return "multiple_questions"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild strict Composer v3 ASK dataset")
    parser.add_argument(
        "--inputs",
        nargs="*",
        default=[
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "ask_generated.jsonl"),
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "train.jsonl"),
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_adversarial_contract.jsonl"),
        ],
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_strict.jsonl"),
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_ask_rebuild_report.json"),
    )
    args = parser.parse_args()

    in_paths = [Path(p) for p in args.inputs if Path(p).exists()]
    if not in_paths:
        raise SystemExit("No input files found.")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    read_records = 0
    written = 0
    rejects: Counter[str] = Counter()
    seen: set[str] = set()

    with out_path.open("w", encoding="utf-8") as out_f:
        for p in in_paths:
            for rec in _load_jsonl(p):
                read_records += 1
                user, assistant = _extract_user_assistant(rec)
                if not user or not assistant:
                    rejects["missing_user_or_assistant"] += 1
                    continue
                reason = _validate_ask(assistant)
                if reason:
                    rejects[reason] += 1
                    continue
                key = f"{user}\n{assistant.strip()}"
                if key in seen:
                    continue
                seen.add(key)
                out_rec = {
                    "messages": [
                        {"role": "system", "content": COMPOSER_V2_SYSTEM_PROMPT_ASK},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": assistant.strip()},
                    ]
                }
                out_f.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
                written += 1

    report = {
        "read_records": read_records,
        "written_records": written,
        "rejected_records": int(sum(rejects.values())),
        "reject_reasons": dict(rejects),
        "inputs": [str(p) for p in in_paths],
        "output": str(out_path),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
