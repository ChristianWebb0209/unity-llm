# RAG Service Testing: Unity Composer vs GPT-4.1-mini

This folder implements qualitative and quantitative comparison between:

- **RAG** (`POST /query`): full RAG pipeline + tool loop, default model `gpt-4.1-mini`
- **Unity Composer** (`POST /composer/query`): fine-tuned model that returns tool_calls directly

## Prerequisites

1. RAG service running, e.g. from `rag_service`:
   ```bash
   uvicorn app.main:app --reload
   ```
2. Optional env for Composer model (if different from server default):
   - `COMPOSER_MODEL` – model name for Composer (e.g. `unity-composer`)
   - `RAG_MODEL` – model for RAG (default `gpt-4.1-mini`)
   - `RAG_BASE_URL` – base URL (default `http://127.0.0.1:8000`)

## Running tests

From `fine_tuning` directory:

```bash
# Run all prompts against both backends (saves to testing/results/)
python -m testing.run

# Run only RAG or only Composer
python -m testing.run --backend rag
python -m testing.run --backend composer

# Limit to first 5 prompts
python -m testing.run --limit 5

# Custom output path
python -m testing.run --out my_run.json
```

Results are written to `testing/results/run_<timestamp>.json` and optionally per-prompt files.

## Generating a report

```bash
# Print summary + side-by-side to stdout
python -m testing.report testing/results/run_20250115_120000.json

# Write Markdown report
python -m testing.report testing/results/run_20250115_120000.json --out report.md

# Summary metrics only
python -m testing.report testing/results/run_20250115_120000.json --summary-only
```

## What gets measured

- **Tool calls**: count and validity (non-empty name, dict arguments)
- **Code blocks**: number of ```csharp blocks and a simple parseability check (balanced brackets)
- **Context usage**: from server (model, token estimates)

For qualitative comparison, use the side-by-side report and/or run with `--limit` and review answers manually.

## Composer v3 gates (dataset + live inference)

From the **repo root**:

```bash
# Pre-train: validate strict JSONL + audit (no server required)
python fine_tuning/scripts/v3/run_composer_v3_pretrain_gates.py

# Post-train: inference contract suite via RAG /composer/query (requires rag_service + composer host)
python fine_tuning/scripts/v3/run_composer_v3_posttrain_release_gate.py
```

Exit codes for the post-train gate / suite:

- `0` — contract suite **passed**
- `2` — contract violations (unknown tools, bad ASK/AGENT behavior)
- `3` — **infrastructure**: RAG or composer upstream unreachable (results in `composer_v3_posttrain_release_gate.json` with `status: skipped`)

**Optional LLM-as-judge** (quantitative preference):

```bash
# Generate judge prompts (no API call); inspect or run manually
python -m testing.judge testing/results/run_20250115_120000.json

# Call OpenAI to get A/B/TIE verdict per prompt (requires OPENAI_API_KEY)
python -m testing.judge testing/results/run_20250115_120000.json --openai --out judge_results.json
```

## Switching backends in the app

The plugin and server already support switching:

- **Plugin**: Settings → Backend profile: "RAG (OpenAI)" vs "Unity Composer". Each profile uses a different endpoint (`/query` vs `/composer/query`).
- **Server**: Same `QueryRequest` body for both; optional `model` in the payload overrides `OPENAI_MODEL` per request. Composer endpoint uses the same client but typically with a different model name (e.g. fine-tuned).

No code changes are required to switch; use the dropdown in the plugin or call the desired endpoint from this harness.
