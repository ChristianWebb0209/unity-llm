"""
Quantitative metrics for test runs: tool validity, code parse, optional LLM-as-judge.
"""
import re
from typing import Any, Dict, List, Optional


def tool_calls_valid(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Check that each tool call has a non-empty name and a dict for arguments.
    Returns { "valid": bool, "count": int, "errors": [...] }.
    """
    errors: List[str] = []
    for i, tc in enumerate(tool_calls or []):
        name = (tc.get("tool_name") or tc.get("name") or "").strip()
        args = tc.get("arguments") if "arguments" in tc else tc.get("args")
        if not name:
            errors.append(f"Tool call {i}: missing tool name")
        if args is not None and not isinstance(args, dict):
            errors.append(f"Tool call {i} ({name}): arguments must be a dict")
    valid = len(errors) == 0
    return {
        "valid": valid,
        "count": len(tool_calls or []),
        "errors": errors,
    }


def extract_gdscript_blocks(text: str) -> List[str]:
    """Extract ```gdscript ... ``` or ``` ... ``` blocks from answer text."""
    if not text:
        return []
    # Match ```gdscript ... ``` or ``` ... ```
    pattern = r"```(?:gdscript)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches if m.strip()]


def code_blocks_parse_attempt(blocks: List[str]) -> Dict[str, Any]:
    """
    Try to detect obvious parse errors (unbalanced brackets, invalid keywords).
    Does not run Godot parser; just heuristics. Returns { "blocks": n, "parseable": n, "issues": [...] }.
    """
    issues: List[str] = []
    parseable = 0
    for i, block in enumerate(blocks):
        block = block.strip()
        if not block:
            continue
        # Very basic: balanced braces
        if block.count("(") != block.count(")"):
            issues.append(f"Block {i}: unbalanced parentheses")
        elif block.count("[") != block.count("]"):
            issues.append(f"Block {i}: unbalanced brackets")
        elif block.count("{") != block.count("}"):
            issues.append(f"Block {i}: unbalanced braces")
        else:
            parseable += 1
    return {
        "blocks": len(blocks),
        "parseable": parseable,
        "issues": issues,
    }


def compute_response_metrics(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a query response (answer, tool_calls, context_usage), compute metrics.
    """
    answer = (response.get("answer") or "").strip()
    tool_calls = response.get("tool_calls") or []
    usage = response.get("context_usage") or {}

    tool_metrics = tool_calls_valid(
        [{"tool_name": tc.get("tool_name"), "arguments": tc.get("arguments") or {}}
         for tc in tool_calls]
    )
    blocks = extract_gdscript_blocks(answer)
    code_metrics = code_blocks_parse_attempt(blocks)

    return {
        "answer_length": len(answer),
        "tool_calls": tool_metrics,
        "code_blocks": code_metrics,
        "context_usage": usage,
    }


def llm_judge_prompt(question: str, answer_a: str, answer_b: str, name_a: str, name_b: str) -> str:
    """Build a prompt for an LLM judge to compare two answers."""
    return f"""You are comparing two assistant answers to the same Godot development question.

Question:
{question}

Answer A ({name_a}):
{answer_a[:4000]}

Answer B ({name_b}):
{answer_b[:4000]}

Which answer is more helpful and correct for a Godot developer? Reply with exactly one line:
A - if Answer A is better
B - if Answer B is better
TIE - if they are roughly equal.
Then briefly explain in 1-2 sentences."""
