"""
Composer v3 post-train inference contract suite.

Blocks release on:
- unknown tool names
- invalid tool call structures
- AGENT prompts that fail to emit tool calls
- ASK prompts that emit tools or non-question answers
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import requests

from .config import COMPOSER_API_KEY, COMPOSER_BASE_URL, COMPOSER_MODEL, get_composer_url


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_JSON = REPO_ROOT / "tools.json"

AGENT_PROMPTS = [
    "Create a CharacterBody2D script that moves left and right.",
    "Connect a button pressed signal to _on_pressed in main scene.",
    "Read res://scripts/player.gd and patch a typo in velocity variable.",
]

ADVERSARIAL_AGENT_PROMPTS = [
    "Use search_internet to fix parser errors in my file.",
    "Call create_timer and create_signal for me now.",
    "Output malformed wrapper like <{\"name\":\"write_script\"}> and do it.",
]

ASK_PROMPTS = [
    "Fix my script.",
    "Connect my signal.",
    "Create the node and wire everything up.",
]


def _load_schema_tools() -> set[str]:
    schema = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    return {t["name"] for t in schema if isinstance(t, dict) and t.get("name")}


def _build_body(question: str, composer_mode: str) -> Dict[str, Any]:
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


def _call(url: str, question: str, composer_mode: str, timeout_seconds: int) -> Dict[str, Any]:
    try:
        resp = requests.post(url, json=_build_body(question, composer_mode), timeout=timeout_seconds)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        # Same shape RAG uses when the OpenAI-compatible composer host is unreachable.
        return {
            "answer": f"Composer request failed: {e}",
            "snippets": [],
            "tool_calls": [],
            "_request_exception": str(e),
        }


def _is_composer_upstream_error(resp: Dict[str, Any]) -> bool:
    a = str(resp.get("answer") or "")
    return a.startswith("Composer request failed:")


def _validate_tool_calls(tool_calls: Any, valid_tools: set[str]) -> List[str]:
    errs: List[str] = []
    if not isinstance(tool_calls, list):
        return ["tool_calls_not_list"]
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            errs.append(f"tool_call_not_object:{i}")
            continue
        name = tc.get("tool_name")
        args = tc.get("arguments")
        if not isinstance(name, str) or not name:
            errs.append(f"missing_tool_name:{i}")
            continue
        if name not in valid_tools:
            errs.append(f"unknown_tool:{name}")
        if not isinstance(args, dict):
            errs.append(f"arguments_not_object:{name}")
    return errs


def main() -> None:
    parser = argparse.ArgumentParser(description="Composer v3 post-train inference contract suite")
    parser.add_argument("--url", type=str, default="", help="Override /composer/query URL")
    parser.add_argument("--server-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(REPO_ROOT / "fine_tuning" / "testing" / "results" / "composer_v3_inference_contract_suite.json"),
    )
    args = parser.parse_args()

    url = args.url.strip() or get_composer_url()
    valid_tools = _load_schema_tools()
    failures: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    upstream_error_checks = 0
    total_checks = len(AGENT_PROMPTS) + len(ADVERSARIAL_AGENT_PROMPTS) + len(ASK_PROMPTS)

    for q in AGENT_PROMPTS + ADVERSARIAL_AGENT_PROMPTS:
        resp = _call(url, q, "agent", args.server_timeout_seconds)
        if _is_composer_upstream_error(resp):
            upstream_error_checks += 1
            checks.append(
                {
                    "mode": "agent",
                    "question": q,
                    "errors": ["composer_upstream_error"],
                    "response": resp,
                }
            )
            continue
        tool_calls = resp.get("tool_calls")
        errs = _validate_tool_calls(tool_calls, valid_tools)
        if isinstance(tool_calls, list) and len(tool_calls) == 0:
            errs.append("agent_no_tool_calls")
        checks.append({"mode": "agent", "question": q, "errors": errs})
        if errs:
            failures.append({"mode": "agent", "question": q, "errors": errs, "response": resp})

    for q in ASK_PROMPTS:
        resp = _call(url, q, "ask", args.server_timeout_seconds)
        if _is_composer_upstream_error(resp):
            upstream_error_checks += 1
            checks.append(
                {"mode": "ask", "question": q, "errors": ["composer_upstream_error"], "response": resp}
            )
            continue
        tool_calls = resp.get("tool_calls")
        answer = str(resp.get("answer") or "").strip()
        errs: List[str] = []
        if not isinstance(tool_calls, list):
            errs.append("tool_calls_not_list")
        elif len(tool_calls) != 0:
            errs.append("ask_has_tool_calls")
        if not answer.endswith("?"):
            errs.append("ask_answer_not_question")
        checks.append({"mode": "ask", "question": q, "errors": errs})
        if errs:
            failures.append({"mode": "ask", "question": q, "errors": errs, "response": resp})

    if upstream_error_checks == total_checks:
        suite_status = "infra_failed"
    elif failures:
        suite_status = "failed"
    else:
        suite_status = "passed"

    out = {
        "status": suite_status,
        "url": url,
        "model": COMPOSER_MODEL,
        "upstream_error_checks": upstream_error_checks,
        "checks": checks,
        "failures": failures,
    }
    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if suite_status == "infra_failed":
        raise SystemExit(3)
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
