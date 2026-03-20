"""
Optional LLM-as-judge: compare RAG vs Composer answers for each prompt.
Usage:
  python -m testing.judge results/run_20250115_120000.json
  python -m testing.judge results/run_20250115_120000.json --openai   # call OpenAI to get verdict
  python -m testing.judge results/run_20250115_120000.json --out judge_results.json

With --openai, uses OPENAI_API_KEY and a small model (e.g. gpt-4.1-mini) to produce A/B/TIE.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .metrics import llm_judge_prompt
from .report import load_run

# Load rag_service/.env when running tests directly (python -m testing.judge)
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env")
load_dotenv(_repo_root / "rag_service" / ".env", override=True)
load_dotenv(_repo_root / "fine_tuning" / ".env", override=True)


def get_rag_and_composer(results: List[Dict[str, Any]], prompt_id: str) -> Optional[tuple]:
    rag = next((r for r in results if r.get("prompt_id") == prompt_id and r.get("backend") == "rag"), None)
    composer = next((r for r in results if r.get("prompt_id") == prompt_id and r.get("backend") == "composer"), None)
    if rag is None or composer is None:
        return None
    return rag, composer


def run_judge_one(
    question: str, answer_a: str, answer_b: str, name_a: str, name_b: str
) -> tuple[Optional[str], Optional[str]]:
    """
    Call OpenAI to get A/B/TIE verdict.
    Returns (verdict_text, error). If OPENAI_API_KEY is missing, returns (None, "missing_openai_api_key").
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None, "openai_package_not_installed"
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None, "missing_openai_api_key"

    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = llm_judge_prompt(question, answer_a, answer_b, name_a, name_b)
    model = os.getenv("JUDGE_MODEL", "gpt-4.1-mini")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return (r.choices[0].message.content or "").strip(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-as-judge: compare RAG vs Composer answers")
    parser.add_argument("run_json", type=str, help="Path to run_<timestamp>.json")
    parser.add_argument("--openai", action="store_true", help="Call OpenAI to get verdict (requires OPENAI_API_KEY)")
    parser.add_argument("--out", type=str, default=None, help="Write judge results here (default: stdout or run_judge_<timestamp>.json)")
    args = parser.parse_args()

    path = Path(args.run_json)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    run = load_run(path)
    results: List[Dict[str, Any]] = run.get("results") or []
    prompt_ids = sorted({r.get("prompt_id") for r in results if r.get("prompt_id")})

    judge_results: List[Dict[str, Any]] = []
    for pid in prompt_ids:
        pair = get_rag_and_composer(results, pid)
        if pair is None:
            continue
        rag, composer = pair
        question = rag.get("question", "")
        ans_rag = (rag.get("response") or {}).get("answer", "")
        ans_composer = (composer.get("response") or {}).get("answer", "")
        prompt_text = llm_judge_prompt(question, ans_rag, ans_composer, "RAG (GPT-4.1-mini)", "Unity Composer")
        entry: Dict[str, Any] = {
            "prompt_id": pid,
            "question": question[:200],
            "verdict": None,
            "explanation": None,
        }
        if args.openai:
            verdict_text, judge_error = run_judge_one(
                question, ans_rag, ans_composer, "RAG (GPT-4.1-mini)", "Unity Composer"
            )
            if verdict_text:
                entry["verdict_raw"] = verdict_text
                first_line = verdict_text.split("\n")[0].strip().upper()
                if "A -" in first_line or first_line.startswith("A "):
                    entry["verdict"] = "A"
                    entry["winner"] = "rag"
                elif "B -" in first_line or first_line.startswith("B "):
                    entry["verdict"] = "B"
                    entry["winner"] = "composer"
                else:
                    entry["verdict"] = "TIE"
                    entry["winner"] = None
            else:
                entry["judge_error"] = judge_error or "unknown_judge_error"
        else:
            entry["judge_prompt"] = prompt_text
        judge_results.append(entry)

    out = {"run": path.name, "judge_results": judge_results}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote judge results to {args.out}")
    else:
        print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
