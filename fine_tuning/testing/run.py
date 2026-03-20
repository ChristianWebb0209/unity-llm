"""
Run test prompts against RAG (GPT-4.1-mini) and/or Godot Composer, save results for comparison.
Usage:
  cd fine_tuning && python -m testing.run
  cd fine_tuning && python -m testing.run --backend rag
  cd fine_tuning && python -m testing.run --backend composer
  cd fine_tuning && python -m testing.run --limit 5

Requires: RAG service running (e.g. uvicorn app.main:app).
Results saved to testing/results/ (JSON per run + one combined run_<timestamp>.json).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

from .config import (
    RAG_MODEL,
    COMPOSER_MODEL,
    COMPOSER_API_KEY,
    COMPOSER_BASE_URL,
    get_rag_url,
    get_composer_url,
)
from .metrics import compute_response_metrics

# Load prompts from YAML next to this package
TESTING_DIR = Path(__file__).resolve().parent
PROMPTS_PATH = TESTING_DIR / "prompts.yaml"
RESULTS_DIR = TESTING_DIR / "results"


def load_prompts(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    prompts = data.get("prompts") or []
    if limit is not None:
        prompts = prompts[:limit]
    return prompts


def build_request_body(
    question: str,
    model: Optional[str],
    backend: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "question": question,
        "top_k": 5,
    }
    if model:
        body["model"] = model
    # Allow per-backend credentials/endpoint overrides (e.g. Vast.ai-hosted Composer).
    if api_key:
        body["api_key"] = api_key
    if base_url:
        body["base_url"] = base_url
    # Minimal context for reproducibility (no project-specific state)
    body["context"] = {"language": "gdscript"}
    return body


def call_backend(
    url: str,
    question: str,
    model: Optional[str],
    backend: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    body = build_request_body(question, model, backend, api_key=api_key, base_url=base_url)
    try:
        r = requests.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {
            "answer": f"[Request failed: {e}]",
            "snippets": [],
            "tool_calls": [],
            "context_usage": {},
            "_error": str(e),
        }


def run_one(
    prompt: Dict[str, Any],
    backend: str,
    url: str,
    model: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
) -> Dict[str, Any]:
    question = prompt.get("question", "")
    pid = prompt.get("id", "unknown")
    response = call_backend(
        url,
        question,
        model,
        backend,
        api_key=api_key,
        base_url=base_url,
    )
    metrics = compute_response_metrics(response)
    return {
        "prompt_id": pid,
        "category": prompt.get("category"),
        "question": question,
        "backend": backend,
        "model": model,
        "response": {
            "answer": response.get("answer", ""),
            "tool_calls": response.get("tool_calls", []),
            "context_usage": response.get("context_usage", {}),
        },
        "metrics": metrics,
        "_error": response.get("_error"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run test prompts against RAG and/or Composer")
    parser.add_argument(
        "--backend",
        choices=["both", "rag", "composer"],
        default="both",
        help="Which backend(s) to call",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of prompts (default: all)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output JSON path (default: results/run_<timestamp>.json)",
    )
    parser.add_argument(
        "--no-save-individual",
        action="store_true",
        help="Do not save per-prompt JSON files in results/",
    )
    args = parser.parse_args()

    prompts = load_prompts(limit=args.limit)
    if not prompts:
        print("No prompts loaded from", PROMPTS_PATH)
        sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    all_results: List[Dict[str, Any]] = []

    backends: List[tuple] = []
    if args.backend in ("both", "rag"):
        # RAG uses server-side OPENAI_* env; no per-request overrides needed here.
        backends.append(("rag", get_rag_url(), RAG_MODEL, None, None))
    if args.backend in ("both", "composer"):
    # Composer can be hosted on Vast.ai (or another OpenAI-compatible host).
        backends.append(
            ("composer", get_composer_url(), COMPOSER_MODEL, COMPOSER_API_KEY, COMPOSER_BASE_URL)
        )

    for prompt in prompts:
        question = prompt.get("question", "")
        pid = prompt.get("id", "unknown")
        print(f"  [{pid}] {question[:60]}...")
        for backend, url, model, api_key, base_url in backends:
            result = run_one(prompt, backend, url, model, api_key, base_url)
            all_results.append(result)
            if not args.no_save_individual:
                out_path = RESULTS_DIR / f"{pid}_{backend}_{timestamp}.json"
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

    out_path = Path(args.out) if args.out else RESULTS_DIR / f"run_{timestamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "backend": args.backend,
                "prompts_count": len(prompts),
                "results": all_results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Wrote {len(all_results)} results to {out_path}")


if __name__ == "__main__":
    main()
