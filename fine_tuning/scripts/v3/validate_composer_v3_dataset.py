#!/usr/bin/env python3
"""
Strict validator for Composer v3 datasets.

Validates AGENT and ASK records against:
- fine_tuning/schemas/tools.json
- fine_tuning/schemas/composer_v3_tool_contract.json
- fine_tuning/schemas/composer_v3_tool_aliases.json
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
DATA_DIR = REPO_ROOT / "fine_tuning" / "data" / "composer_v3"
TOOLS_JSON = REPO_ROOT / "fine_tuning" / "schemas" / "tools.json"
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


def _extract_messages(record: Dict[str, Any]) -> Tuple[str, str, str]:
    system = ""
    user = ""
    assistant = ""
    msgs = record.get("messages") or []
    if not isinstance(msgs, list):
        return system, user, assistant
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "system":
            system = content
        elif role == "user":
            user = content
        elif role == "assistant":
            assistant = content
    return system, user, assistant


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


def _extract_tool_inners(assistant_content: str) -> List[str]:
    return [
        s.strip() for s in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", assistant_content or "", flags=re.DOTALL)
    ]


def _strip_non_contract_blocks(assistant_content: str, allow_think_blocks: bool) -> str:
    s = assistant_content or ""
    s = re.sub(r"<tool_call>\s*.*?\s*</tool_call>", "", s, flags=re.DOTALL)
    if allow_think_blocks:
        s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL)
    return s.strip()


def _validate_agent_assistant(
    assistant: str,
    schema_by_name: Dict[str, Dict[str, Any]],
    contract_tools: set[str],
    aliases: Dict[str, Optional[str]],
    allow_think_blocks: bool,
) -> List[str]:
    errors: List[str] = []
    if "__OPTIONS__" in assistant:
        errors.append("contains___OPTIONS__")
    inners = _extract_tool_inners(assistant)
    if not inners:
        errors.append("no_tool_call_blocks")
        return errors
    leftover = _strip_non_contract_blocks(assistant, allow_think_blocks=allow_think_blocks)
    if leftover:
        errors.append("extra_text_outside_tool_call_blocks")

    for inner in inners:
        try:
            payload = json.loads(inner)
        except json.JSONDecodeError:
            errors.append("tool_call_inner_json_invalid")
            continue
        if not isinstance(payload, dict):
            errors.append("tool_call_inner_not_object")
            continue
        name = payload.get("name")
        args = payload.get("arguments")
        if not name or not isinstance(name, str):
            errors.append("missing_tool_name")
            continue
        if name in aliases:
            errors.append(f"alias_tool_forbidden:{name}")
        if name not in contract_tools:
            errors.append(f"tool_not_in_v3_contract:{name}")
        schema = schema_by_name.get(name)
        if schema is None:
            errors.append(f"tool_not_in_schema:{name}")
            continue
        if not isinstance(args, dict):
            errors.append(f"arguments_not_object:{name}")
            continue
        params = schema.get("parameters") or {}
        required = params.get("required") or []
        for req in required:
            if req not in args:
                errors.append(f"missing_required_arg:{name}:{req}")
        props = params.get("properties") or {}
        if isinstance(props, dict):
            for k, v in args.items():
                spec = props.get(k)
                if not isinstance(spec, dict):
                    continue
                t = spec.get("type")
                if t and not _type_matches(str(t), v):
                    errors.append(f"arg_type_mismatch:{name}:{k}")
    return errors


def _validate_ask_assistant(assistant: str) -> List[str]:
    s = (assistant or "").strip()
    errors: List[str] = []
    if not s:
        errors.append("empty_assistant")
        return errors
    if "<tool_call>" in s or "</tool_call>" in s:
        errors.append("contains_tool_call_blocks")
    if "__OPTIONS__" in s:
        errors.append("contains___OPTIONS__")
    if not s.endswith("?"):
        errors.append("does_not_end_with_question_mark")
    if "\n" in s:
        errors.append("contains_newlines")
    if s.count("?") != 1:
        errors.append("multiple_or_missing_questions")
    return errors


def validate_dataset(
    input_path: Path,
    mode: str,
    schema_by_name: Dict[str, Dict[str, Any]],
    contract_tools: set[str],
    aliases: Dict[str, Optional[str]],
    allow_think_blocks: bool,
) -> Dict[str, Any]:
    error_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    total = 0
    valid = 0
    invalid_examples: List[Dict[str, Any]] = []

    for idx, rec in enumerate(_load_jsonl(input_path), start=1):
        total += 1
        _system, _user, assistant = _extract_messages(rec)
        if mode == "agent":
            errors = _validate_agent_assistant(
                assistant=assistant,
                schema_by_name=schema_by_name,
                contract_tools=contract_tools,
                aliases=aliases,
                allow_think_blocks=allow_think_blocks,
            )
            for inner in _extract_tool_inners(assistant):
                try:
                    p = json.loads(inner)
                except Exception:
                    continue
                if isinstance(p, dict):
                    n = p.get("name")
                    if isinstance(n, str) and n:
                        tool_counter[n] += 1
        else:
            errors = _validate_ask_assistant(assistant=assistant)

        if errors:
            for e in errors:
                error_counter[e] += 1
            if len(invalid_examples) < 25:
                invalid_examples.append({"line": idx, "errors": errors})
        else:
            valid += 1

    return {
        "mode": mode,
        "input_path": str(input_path),
        "total_records": total,
        "valid_records": valid,
        "invalid_records": total - valid,
        "error_counts": dict(error_counter),
        "tool_frequency": dict(tool_counter),
        "sample_invalid_examples": invalid_examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict Composer v3 dataset validator")
    parser.add_argument(
        "--mode",
        choices=["agent", "ask", "both"],
        default="both",
    )
    parser.add_argument(
        "--agent-input",
        type=str,
        default=str(DATA_DIR / "agent_strict.jsonl"),
        help="Path to AGENT JSONL or mixed train JSONL.",
    )
    parser.add_argument(
        "--ask-input",
        type=str,
        default=str(DATA_DIR / "ask_strict.jsonl"),
        help="Path to ASK JSONL.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_dataset_validation.json"),
    )
    args = parser.parse_args()

    schema = _load_json(TOOLS_JSON)
    contract = _load_json(CONTRACT_JSON)
    aliases_doc = _load_json(ALIASES_JSON)

    schema_by_name = {t["name"]: t for t in schema if isinstance(t, dict) and t.get("name")}
    contract_tools = set(contract.get("tools") or [])
    allow_think_blocks = bool((contract.get("agent_format") or {}).get("allow_think_blocks", False))
    aliases = (aliases_doc.get("aliases") or {}) if isinstance(aliases_doc, dict) else {}

    out: Dict[str, Any] = {
        "validator": "validate_composer_v3_dataset.py",
        "contract_version": contract.get("version"),
        "results": {},
    }
    exit_code = 0

    if args.mode in ("agent", "both"):
        agent_report = validate_dataset(
            input_path=Path(args.agent_input),
            mode="agent",
            schema_by_name=schema_by_name,
            contract_tools=contract_tools,
            aliases=aliases,
            allow_think_blocks=allow_think_blocks,
        )
        out["results"]["agent"] = agent_report
        if agent_report["invalid_records"] > 0:
            exit_code = 2

    if args.mode in ("ask", "both"):
        ask_report = validate_dataset(
            input_path=Path(args.ask_input),
            mode="ask",
            schema_by_name=schema_by_name,
            contract_tools=contract_tools,
            aliases=aliases,
            allow_think_blocks=allow_think_blocks,
        )
        out["results"]["ask"] = ask_report
        if ask_report["invalid_records"] > 0:
            exit_code = 2

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(out, indent=2, ensure_ascii=False))
    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
