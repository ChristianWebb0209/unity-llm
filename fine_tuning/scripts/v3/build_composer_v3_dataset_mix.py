#!/usr/bin/env python3
"""
Build Composer v3 mixed train/val with deterministic split.
Default ratio favors strict AGENT behavior (90/10).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]


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


def _hash(user: str, assistant: str) -> int:
    h = hashlib.sha256((user + "\n" + assistant).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _select_deterministic(records: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    if n >= len(records):
        return records
    scored = []
    for r in records:
        u, a = _extract_user_assistant(r)
        scored.append((_hash(u, a), r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored[:n]]


def _split_train_val(records: List[Dict[str, Any]], val_ratio: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    train: List[Dict[str, Any]] = []
    val: List[Dict[str, Any]] = []
    mod = 1_000_000
    val_bucket = int(val_ratio * mod)
    for r in records:
        u, a = _extract_user_assistant(r)
        if (_hash(u, a) % mod) < val_bucket:
            val.append(r)
        else:
            train.append(r)
    return train, val


def _dedupe(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for r in records:
        u, a = _extract_user_assistant(r)
        key = f"{u}\n{a}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Composer v3 train/val mix")
    parser.add_argument(
        "--agent-file",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "agent_strict.jsonl"),
    )
    parser.add_argument(
        "--ask-file",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_strict.jsonl"),
    )
    parser.add_argument("--agent-ratio", type=float, default=0.9)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument(
        "--output-train",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "train.jsonl"),
    )
    parser.add_argument(
        "--output-val",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "val.jsonl"),
    )
    args = parser.parse_args()

    agent_records = _dedupe(list(_load_jsonl(Path(args.agent_file))))
    ask_records = _dedupe(list(_load_jsonl(Path(args.ask_file))))
    if not agent_records:
        raise SystemExit("No agent records found.")
    if not ask_records:
        raise SystemExit("No ask records found.")

    ask_ratio = 1.0 - args.agent_ratio
    max_total_by_agent = int(len(agent_records) / args.agent_ratio) if args.agent_ratio > 0 else 0
    max_total_by_ask = int(len(ask_records) / ask_ratio) if ask_ratio > 0 else 0
    total_target = min(max_total_by_agent, max_total_by_ask)
    if total_target <= 0:
        raise SystemExit("Invalid ratio or insufficient data.")

    target_agent = int(total_target * args.agent_ratio)
    target_ask = total_target - target_agent
    selected_agent = _select_deterministic(agent_records, target_agent)
    selected_ask = _select_deterministic(ask_records, target_ask)
    mixed = selected_agent + selected_ask
    train, val = _split_train_val(mixed, args.val_ratio)

    train_out = Path(args.output_train)
    val_out = Path(args.output_val)
    train_out.parent.mkdir(parents=True, exist_ok=True)
    train_out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train), encoding="utf-8")
    val_out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in val), encoding="utf-8")

    print("Composer v3 dataset mix complete.")
    print(f"Agent pool: {len(agent_records)} -> selected: {len(selected_agent)}")
    print(f"Ask pool:   {len(ask_records)} -> selected: {len(selected_ask)}")
    print(f"Total: {len(mixed)} | Train: {len(train)} | Val: {len(val)}")
    print(f"Wrote train: {train_out}")
    print(f"Wrote val:   {val_out}")


if __name__ == "__main__":
    main()
