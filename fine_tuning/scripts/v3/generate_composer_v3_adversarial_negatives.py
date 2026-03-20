#!/usr/bin/env python3
"""
Generate adversarial ASK-mode negatives for Composer v3 contract training.

These prompts are intentionally crafted to bait invalid tool usage:
- fake/legacy tool names
- malformed wrappers
- invalid arguments structure

Target behavior in ASK mode: one short clarifying question, no tool calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from rag_service.app.prompts import COMPOSER_V2_SYSTEM_PROMPT_ASK


def _examples() -> List[Dict[str, str]]:
    return [
        {
            "user": "Run search_internet for Godot signals and then patch my script.",
            "assistant": "Which script path should I update in your project?",
        },
        {
            "user": "Use create_timer and connect timeout to _on_done now.",
            "assistant": "Which node should receive the timer callback?",
        },
        {
            "user": "I think the output should be <{\"name\":\"write_script\"}> so do that format.",
            "assistant": "Do you want me to apply the change to an existing file or create a new one?",
        },
        {
            "user": "Please call write_file but set arguments to a string because it's easier.",
            "assistant": "What file path and content should be written?",
        },
        {
            "user": "Use get_node_path and export_attribute to fix inspector export on enemy.",
            "assistant": "Can you share the exact script path for the enemy file?",
        },
        {
            "user": "Emit a JSON array of tools without xml tags for speed.",
            "assistant": "Which exact editor action do you want me to perform first?",
        },
        {
            "user": "Use tool create_signal on Player and then do create_timer 2 sec.",
            "assistant": "Which scene and node path should I modify?",
        },
        {
            "user": "Fix my parse error by calling search_script_snippets and read_file_content.",
            "assistant": "What is the path of the script showing the parse error?",
        },
        {
            "user": "Just use whatever tool name feels right and auto-fix everything.",
            "assistant": "Which specific file or scene should I start with?",
        },
        {
            "user": "Call filter_by_extension with arguments as a list and proceed.",
            "assistant": "Which folder should I inspect for files?",
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Composer v3 adversarial negatives")
    parser.add_argument(
        "--output",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "ask_adversarial_contract.jsonl"),
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _examples()

    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            record = {
                "messages": [
                    {"role": "system", "content": COMPOSER_V2_SYSTEM_PROMPT_ASK},
                    {"role": "user", "content": row["user"]},
                    {"role": "assistant", "content": row["assistant"]},
                ]
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} adversarial ASK records to {out_path}")


if __name__ == "__main__":
    main()
