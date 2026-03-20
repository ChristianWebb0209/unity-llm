#!/usr/bin/env python3
"""
Rebuild Composer v3 AGENT dataset with strict schema/contract enforcement.

Pipeline:
- read one or more AGENT-like JSONL files
- parse <tool_call> blocks
- canonicalize known aliases
- reject unmapped/unknown tools and invalid argument structures
- emit normalized AGENT records
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from rag_service.app.prompts import COMPOSER_V2_SYSTEM_PROMPT_AGENT


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


def _extract_tool_blocks(assistant: str) -> List[str]:
    return [s.strip() for s in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", assistant or "", flags=re.DOTALL)]


def _type_matches(expected_type: str, value: Any) -> bool:
    t = expected_type.lower()
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def _normalize_tool_calls(
    assistant: str,
    schema_by_name: Dict[str, Dict[str, Any]],
    contract_tools: set[str],
    alias_map: Dict[str, Optional[str]],
    allow_think_blocks: bool,
    remap_counter: Counter[str],
) -> Tuple[Optional[str], Optional[str]]:
    if "__OPTIONS__" in assistant:
        return None, "contains___OPTIONS__"
    blocks = _extract_tool_blocks(assistant)
    if not blocks:
        return None, "missing_tool_call_blocks"
    leftover = re.sub(r"<tool_call>\s*.*?\s*</tool_call>", "", assistant, flags=re.DOTALL)
    if allow_think_blocks:
        leftover = re.sub(r"<think>.*?</think>", "", leftover, flags=re.DOTALL)
    if leftover.strip():
        return None, "extra_text_outside_tool_calls"

    normalized_blocks: List[str] = []
    for inner in blocks:
        try:
            payload = json.loads(inner)
        except json.JSONDecodeError:
            return None, "tool_call_json_invalid"
        if not isinstance(payload, dict):
            return None, "tool_call_payload_not_object"
        name = payload.get("name")
        args = payload.get("arguments")
        if not isinstance(name, str) or not name:
            return None, "tool_name_missing"
        if name in alias_map:
            mapped = alias_map[name]
            if not mapped:
                return None, f"unmappable_alias:{name}"
            remap_counter[f"{name}->{mapped}"] += 1
            name = mapped
        if name not in contract_tools:
            return None, f"tool_not_in_contract:{name}"
        schema = schema_by_name.get(name)
        if schema is None:
            return None, f"tool_not_in_schema:{name}"
        if not isinstance(args, dict):
            return None, f"arguments_not_object:{name}"

        params = schema.get("parameters") or {}
        required = params.get("required") or []
        for req in required:
            if req not in args:
                return None, f"missing_required_arg:{name}:{req}"
        props = params.get("properties") or {}
        if isinstance(props, dict):
            for k, v in args.items():
                spec = props.get(k)
                if not isinstance(spec, dict):
                    continue
                t = spec.get("type")
                if t and not _type_matches(str(t), v):
                    return None, f"arg_type_mismatch:{name}:{k}"

        normalized_inner = json.dumps({"name": name, "arguments": args}, ensure_ascii=False, separators=(",", ":"))
        normalized_blocks.append(f"<tool_call>{normalized_inner}</tool_call>")

    normalized = "\n".join(normalized_blocks)
    return normalized, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild strict Composer v3 AGENT dataset")
    parser.add_argument(
        "--inputs",
        nargs="*",
        default=[
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "agent_from_synthetic_v2_generated.jsonl"),
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "agent_from_lint_repairs.jsonl"),
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "agent_generated.jsonl"),
            str(REPO_ROOT / "fine_tuning" / "data" / "composer_v2" / "train.jsonl"),
        ],
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "agent_strict.jsonl"),
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_agent_rebuild_report.json"),
    )
    parser.add_argument(
        "--fail-on-rejected",
        action="store_true",
        help="Exit non-zero if any record is rejected.",
    )
    args = parser.parse_args()

    schema = _load_json(TOOLS_JSON)
    contract = _load_json(CONTRACT_JSON)
    aliases_doc = _load_json(ALIASES_JSON)
    alias_map = aliases_doc.get("aliases") or {}

    schema_by_name = {t["name"]: t for t in schema if isinstance(t, dict) and t.get("name")}
    contract_tools = set(contract.get("tools") or [])
    allow_think_blocks = bool((contract.get("agent_format") or {}).get("allow_think_blocks", False))

    in_paths = [Path(p) for p in args.inputs if Path(p).exists()]
    if not in_paths:
        raise SystemExit("No input files found.")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    remap_counter: Counter[str] = Counter()
    reject_counter: Counter[str] = Counter()
    seen_keys: set[str] = set()
    written = 0
    read_records = 0

    with out_path.open("w", encoding="utf-8") as out_f:
        for path in in_paths:
            for rec in _load_jsonl(path):
                read_records += 1
                user, assistant = _extract_user_assistant(rec)
                if not user or not assistant:
                    reject_counter["missing_user_or_assistant"] += 1
                    continue
                normalized, reason = _normalize_tool_calls(
                    assistant=assistant,
                    schema_by_name=schema_by_name,
                    contract_tools=contract_tools,
                    alias_map=alias_map,
                    allow_think_blocks=allow_think_blocks,
                    remap_counter=remap_counter,
                )
                if not normalized:
                    reject_counter[reason or "invalid"] += 1
                    continue
                key = f"{user}\n{normalized}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                out_rec = {
                    "messages": [
                        {"role": "system", "content": COMPOSER_V2_SYSTEM_PROMPT_AGENT},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": normalized},
                    ]
                }
                out_f.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
                written += 1

    report = {
        "read_records": read_records,
        "written_records": written,
        "rejected_records": int(sum(reject_counter.values())),
        "reject_reasons": dict(reject_counter),
        "remap_counts": dict(remap_counter),
        "inputs": [str(p) for p in in_paths],
        "output": str(out_path),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.fail_on_rejected and report["rejected_records"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
