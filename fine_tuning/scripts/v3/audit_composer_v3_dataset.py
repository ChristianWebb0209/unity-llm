#!/usr/bin/env python3
"""
Audit Composer v3 dataset quality and contract compliance.
Produces:
- machine-readable JSON report
- human-readable Markdown summary
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_JSON = REPO_ROOT / "tools.json"
CONTRACT_JSON = REPO_ROOT / "fine_tuning" / "schemas" / "composer_v3_tool_contract.json"
ALIASES_JSON = REPO_ROOT / "fine_tuning" / "schemas" / "composer_v3_tool_aliases.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            yield json.loads(s)


def _extract_user_assistant(record: Dict[str, Any]) -> Tuple[str, str]:
    user = ""
    assistant = ""
    for m in record.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "user":
            user = content
        elif role == "assistant":
            assistant = content
    return user, assistant


def _extract_tool_inners(text: str) -> List[str]:
    return [s.strip() for s in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text or "", flags=re.DOTALL)]


def _agent_metrics(path: Path, schema_tools: set[str], contract_tools: set[str], alias_tools: set[str]) -> Dict[str, Any]:
    total = 0
    skipped_non_agent = 0
    malformed = 0
    unknown_schema = 0
    unknown_contract = 0
    alias_hits = 0
    tool_counter: Counter[str] = Counter()

    for rec in _load_jsonl(path):
        total += 1
        _user, assistant = _extract_user_assistant(rec)
        if "<tool_call>" not in assistant:
            skipped_non_agent += 1
            continue
        inners = _extract_tool_inners(assistant)
        if not inners:
            malformed += 1
            continue
        for inner in inners:
            try:
                payload = json.loads(inner)
            except Exception:
                malformed += 1
                continue
            name = payload.get("name") if isinstance(payload, dict) else None
            if not isinstance(name, str) or not name:
                malformed += 1
                continue
            tool_counter[name] += 1
            if name not in schema_tools:
                unknown_schema += 1
            if name not in contract_tools:
                unknown_contract += 1
            if name in alias_tools:
                alias_hits += 1

    return {
        "records": total,
        "skipped_non_agent_records": skipped_non_agent,
        "malformed_tool_call_blocks": malformed,
        "unknown_schema_tool_calls": unknown_schema,
        "unknown_contract_tool_calls": unknown_contract,
        "alias_tool_calls": alias_hits,
        "tool_frequency": dict(tool_counter),
    }


def _ask_metrics(path: Path) -> Dict[str, Any]:
    total = 0
    violations = 0
    for rec in _load_jsonl(path):
        total += 1
        _user, assistant = _extract_user_assistant(rec)
        s = assistant.strip()
        if (
            not s
            or "<tool_call>" in s
            or "__OPTIONS__" in s
            or not s.endswith("?")
            or s.count("?") != 1
            or "\n" in s
        ):
            violations += 1
    return {"records": total, "ask_contract_violations": violations}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Composer v3 dataset")
    parser.add_argument("--agent-train", type=str, default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "train.jsonl"))
    parser.add_argument("--agent-base", type=str, default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "agent_strict.jsonl"))
    parser.add_argument("--ask-base", type=str, default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_strict.jsonl"))
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_data_audit.json"),
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_data_audit.md"),
    )
    args = parser.parse_args()

    schema = _load_json(TOOLS_JSON)
    contract = _load_json(CONTRACT_JSON)
    aliases_doc = _load_json(ALIASES_JSON)
    schema_tools = {t["name"] for t in schema if isinstance(t, dict) and t.get("name")}
    contract_tools = set(contract.get("tools") or [])
    alias_tools = set((aliases_doc.get("aliases") or {}).keys())

    report: Dict[str, Any] = {
        "contract_version": contract.get("version"),
        "agent_train": _agent_metrics(Path(args.agent_train), schema_tools, contract_tools, alias_tools),
        "agent_base": _agent_metrics(Path(args.agent_base), schema_tools, contract_tools, alias_tools),
        "ask_base": _ask_metrics(Path(args.ask_base)),
        "inputs": {
            "agent_train": args.agent_train,
            "agent_base": args.agent_base,
            "ask_base": args.ask_base,
        },
    }

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    top_tools = sorted(report["agent_train"]["tool_frequency"].items(), key=lambda kv: -kv[1])[:15]
    lines = [
        "# Composer v3 Data Audit",
        "",
        f"- Contract version: `{report['contract_version']}`",
        f"- Agent train records: `{report['agent_train']['records']}`",
        f"- Ask base records: `{report['ask_base']['records']}`",
        "",
        "## Contract Violations",
        f"- Agent malformed tool blocks: `{report['agent_train']['malformed_tool_call_blocks']}`",
        f"- Agent unknown schema tool calls: `{report['agent_train']['unknown_schema_tool_calls']}`",
        f"- Agent unknown contract tool calls: `{report['agent_train']['unknown_contract_tool_calls']}`",
        f"- Agent alias tool calls (should be 0): `{report['agent_train']['alias_tool_calls']}`",
        f"- Ask contract violations: `{report['ask_base']['ask_contract_violations']}`",
        "",
        "## Top Agent Tools (train)",
    ]
    for name, count in top_tools:
        lines.append(f"- `{name}`: {count}")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote JSON audit: {out_json}")
    print(f"Wrote Markdown audit: {out_md}")


if __name__ == "__main__":
    main()
