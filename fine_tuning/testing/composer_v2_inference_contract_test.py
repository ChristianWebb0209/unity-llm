"""
Composer v2 inference contract test.

Assumes rag_service is running (FastAPI server).

Verifies:
- AGENT mode ("composer_mode": "agent") returns non-empty tool_calls for tool-like prompts.
- ASK mode ("composer_mode": "ask") returns empty tool_calls and a question-like answer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import requests

from .config import COMPOSER_API_KEY, COMPOSER_BASE_URL, COMPOSER_MODEL, get_composer_url


def _build_request_body(question: str, composer_mode: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "question": question,
        "context": {"language": "gdscript"},
        "top_k": 8,
        "model": COMPOSER_MODEL,
        "composer_mode": composer_mode,
    }
    if COMPOSER_API_KEY:
        body["api_key"] = COMPOSER_API_KEY
    if COMPOSER_BASE_URL:
        body["base_url"] = COMPOSER_BASE_URL
    return body


def run_one(url: str, question: str, composer_mode: str, timeout_seconds: int) -> Dict[str, Any]:
    body = _build_request_body(question=question, composer_mode=composer_mode)
    resp = requests.post(url, json=body, timeout=timeout_seconds)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Composer v2 inference contract test")
    parser.add_argument("--server-timeout-seconds", type=int, default=120)
    parser.add_argument("--agent-question", type=str, default="What signals does Button have?")
    parser.add_argument("--ask-question", type=str, default="Connect a button's pressed signal to a method in GDScript.")
    parser.add_argument("--url", type=str, default="", help="Override /composer/query URL")
    args = parser.parse_args()

    url = args.url.strip() or get_composer_url()

    # AGENT mode test
    agent_resp = run_one(
        url=url,
        question=args.agent_question,
        composer_mode="agent",
        timeout_seconds=args.server_timeout_seconds,
    )
    agent_tool_calls = agent_resp.get("tool_calls") or []
    if not isinstance(agent_tool_calls, list):
        raise SystemExit(f"Expected tool_calls to be list; got: {type(agent_tool_calls)}")
    if len(agent_tool_calls) == 0:
        print(json.dumps(agent_resp, indent=2, ensure_ascii=False))
        raise SystemExit("AGENT contract failed: expected non-empty tool_calls.")
    print(f"AGENT OK: tool_calls={len(agent_tool_calls)}")

    # ASK mode test
    ask_resp = run_one(
        url=url,
        question=args.ask_question,
        composer_mode="ask",
        timeout_seconds=args.server_timeout_seconds,
    )
    ask_tool_calls = ask_resp.get("tool_calls") or []
    ask_answer = (ask_resp.get("answer") or "").strip()

    if not isinstance(ask_tool_calls, list):
        raise SystemExit(f"Expected tool_calls to be list; got: {type(ask_tool_calls)}")
    if len(ask_tool_calls) != 0:
        print(json.dumps(ask_resp, indent=2, ensure_ascii=False))
        raise SystemExit("ASK contract failed: expected empty tool_calls.")
    if not ask_answer.endswith("?"):
        print(json.dumps(ask_resp, indent=2, ensure_ascii=False))
        raise SystemExit("ASK contract failed: expected answer to end with '?'.")

    print("ASK OK")


if __name__ == "__main__":
    main()

