"""
Load test run results and print or write a comparison report: side-by-side and summary metrics.
Usage:
  python -m testing.report results/run_20250101_120000.json
  python -m testing.report results/run_20250101_120000.json --out report.md
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

def load_run(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def aggregate_metrics(results: List[Dict[str, Any]], backend: str) -> Dict[str, Any]:
    subset = [r for r in results if r.get("backend") == backend]
    n = len(subset)
    if n == 0:
        return {"count": 0}
    tool_valid = sum(1 for r in subset if (r.get("metrics") or {}).get("tool_calls", {}).get("valid", True))
    total_tools = sum((r.get("metrics") or {}).get("tool_calls", {}).get("count", 0) for r in subset)
    code_blocks = sum((r.get("metrics") or {}).get("code_blocks", {}).get("blocks", 0) for r in subset)
    code_parseable = sum((r.get("metrics") or {}).get("code_blocks", {}).get("parseable", 0) for r in subset)
    errors = sum(1 for r in subset if r.get("_error"))
    return {
        "count": n,
        "errors": errors,
        "tool_calls_valid_pct": (tool_valid / n * 100) if n else 0,
        "total_tool_calls": total_tools,
        "code_blocks_total": code_blocks,
        "code_blocks_parseable": code_parseable,
    }


def format_side_by_side(run: Dict[str, Any]) -> str:
    lines: List[str] = []
    results: List[Dict[str, Any]] = run.get("results") or []
    prompts_count = run.get("prompts_count", 0)
    by_prompt: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        pid = r.get("prompt_id", "?")
        by_prompt.setdefault(pid, []).append(r)

    lines.append("# Test run comparison\n")
    lines.append(f"Timestamp: {run.get('timestamp', '')}")
    lines.append(f"Backends: {run.get('backend', '')}")
    lines.append("")

    for pid in sorted(by_prompt.keys()):
        entries = by_prompt[pid]
        q = entries[0].get("question", "") if entries else ""
        lines.append(f"## {pid}")
        lines.append(f"**Question:** {q}\n")
        for e in entries:
            backend = e.get("backend", "?")
            model = e.get("model", "?")
            ans = (e.get("response") or {}).get("answer", "") or "(no answer)"
            tool_calls = (e.get("response") or {}).get("tool_calls") or []
            metrics = e.get("metrics") or {}
            tc = metrics.get("tool_calls", {})
            err = e.get("_error", "")
            lines.append(f"### {backend} ({model})")
            if err:
                lines.append(f"*Error: {err}*")
            lines.append(f"- Tool calls: {tc.get('count', 0)} (valid: {tc.get('valid', 'N/A')})")
            lines.append(f"- Answer length: {len(ans)} chars")
            # First 500 chars of answer
            preview = ans[:500] + "..." if len(ans) > 500 else ans
            lines.append(f"**Answer preview:**\n{preview}\n")
        lines.append("---")

    return "\n".join(lines)


def format_summary(run: Dict[str, Any]) -> str:
    results: List[Dict[str, Any]] = run.get("results") or []
    lines: List[str] = ["# Summary metrics\n"]
    for backend in ("rag", "composer"):
        agg = aggregate_metrics(results, backend)
        if agg["count"] == 0:
            continue
        lines.append(f"## {backend}")
        lines.append(f"- Prompts: {agg['count']}")
        lines.append(f"- Request errors: {agg['errors']}")
        lines.append(f"- Tool calls valid (responses): {agg['tool_calls_valid_pct']:.1f}%")
        lines.append(f"- Total tool calls: {agg['total_tool_calls']}")
        lines.append(f"- Code blocks (total / parseable): {agg['code_blocks_total']} / {agg['code_blocks_parseable']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate report from test run JSON")
    parser.add_argument("run_json", type=str, help="Path to run_<timestamp>.json")
    parser.add_argument("--out", type=str, default=None, help="Write report to this file (default: stdout)")
    parser.add_argument("--summary-only", action="store_true", help="Print only summary metrics")
    args = parser.parse_args()

    path = Path(args.run_json)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    run = load_run(path)
    if args.summary_only:
        report = format_summary(run)
    else:
        report = format_summary(run) + "\n\n" + format_side_by_side(run)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote report to {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
