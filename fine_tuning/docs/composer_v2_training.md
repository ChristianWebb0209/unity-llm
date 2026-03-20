# Composer v2 Training Plan

## Goal
Make **Godot Composer** behave like a tool-using editor agent that reliably emits structured tool calls.

This v2 focuses on:
1. **Removing “pollution”** from training data (no ambiguous option/ask-style responses mixed into agent behavior).
2. Introducing an explicit **mode** per request: **AGENT** vs **ASK**.
3. **Removing `__OPTIONS__` entirely** from Composer v2.
4. Updating Composer parsing to match what the model is trained to output: **XML `<tool_call>...</tool_call>` blocks**.
5. Biasing toward tool use: **80% AGENT / 20% ASK**.

## Why things went wrong in v1 (root causes)
Observed behavior in `fine_tuning/testing/results/*_composer_*.json`:
- Most Composer responses had `tool_calls: []` even when the request should lead to editor actions.
- Some responses emitted `__OPTIONS__` / clarifying questions instead of tool calls.

The underlying failure mode is a **contract mismatch**:
- Composer runtime parsing previously looked for a **JSON array** at the end of the assistant message.
- Our tool-use fine-tuning data and generators were centered on emitting **XML blocks** like:
  - `<tool_call>{"name": "...", "arguments": {...}}</tool_call>`

When the model produced XML blocks, the runtime didn’t find the expected JSON array → `tool_calls` ended up empty.

On top of that, training examples mixed in tasks where tools weren’t strictly required, making “no tool call” a valid/acceptable outcome.

## Composer v2 Runtime Contract

### Request
- The client sends `composer_mode` in the `/composer/query` request:
  - `"agent"`: model must emit tool calls
  - `"ask"`: model must ask one clarifying question, no tools

### Agent output (AGENT mode)
- Tool calls MUST be emitted inside one or more XML blocks:
  - `<tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>`
- Optional reasoning may be included in `<think>...</think>`, but should not be required.
- No `__OPTIONS__`.
- No conversational trailing content required; tool calls are the primary output.

### Ask output (ASK mode)
- Output exactly **one short clarifying question**.
- No `<tool_call>` blocks.
- No `__OPTIONS__`.

## Data Generation & Translation Strategy

We build a dedicated **Composer v2 dataset** from scratch (or via translation), strictly enforcing format with validators.

### 1) Translate existing tool-use datasets → AGENT mode
Create scripts:
- `fine_tuning/scripts/translate_tool_usage_to_composer_v2_agent.py`
- `fine_tuning/scripts/translate_synthetic_v2_generated_to_composer_v2_agent.py`

These scripts take existing examples that already contain `<tool_call>` blocks and:
- Wrap them into a consistent `{"messages": [system, user, assistant]}` format
- Replace the `system` message with the Composer v2 **AGENT** system prompt
- Filter out any records containing `__OPTIONS__`

### 2) Generate new ASK mode examples (20%)
Create:
- `fine_tuning/scripts/generate_composer_v2_ask_dataset.py`

For each example:
- user prompt is ambiguous / missing details required to safely act in the editor
- assistant output is exactly one question (`?` terminated)
- assistant output must contain **no** `<tool_call>` blocks
- no `__OPTIONS__`

We will generate lots of these examples via OpenAI until the validator acceptance rate is high.

### 3) Generate additional AGENT mode examples (bulk)
Create:
- `fine_tuning/scripts/generate_composer_v2_agent_dataset.py`

Generate many short imperatives that require editor tool actions.
Enforce:
- assistant emits one or more `<tool_call>` blocks
- each `<tool_call>` inner JSON parses
- tool names exist in `tools.json` (repo root)
- arguments are dictionaries and satisfy required keys (schema validation)

### 4) Validate the dataset strictly
Create:
- `fine_tuning/scripts/validate_composer_v2_dataset.py`

It enforces:
- AGENT records: must contain ≥1 `<tool_call>` block
- ASK records: must contain 0 `<tool_call>` blocks
- No `__OPTIONS__`

### 5) Mix + deterministic split (80/20)
Create:
- `fine_tuning/scripts/build_composer_v2_dataset_mix.py`

It merges:
- translated AGENT data
- generated AGENT data
- generated ASK data

Applies 80% AGENT / 20% ASK mixing.

Split train/val deterministically (hash-based) so reruns are stable.

## Training (v2)
Create:
- `fine_tuning/colab/train_lora_composer_v2.py`

This training script:
- loads `fine_tuning/data/composer_v2/train.jsonl` and `val.jsonl`
- trains a LoRA adapter using only Composer v2 dataset (no forums/docs/code_style mixing in v2 iteration 1)

## Evaluation / Regression
Create:
- `fine_tuning/testing/composer_v2_inference_contract_test.py`

It verifies:
- AGENT mode returns non-empty `tool_calls` for a set of prompts
- ASK mode returns empty `tool_calls` and a question-like answer

After contract validation, run the existing harness:
- `fine_tuning/testing/run.py --backend composer`

If failures occur:
- check dataset validator logs
- verify runtime parser extraction for `<tool_call>` blocks
- iterate prompt + output enforcement

## Where Mode is Used
Mode is provided by the client:
- In Godot plugin request building, map UI expectations to:
  - `use_tools == true` → `composer_mode="agent"`
  - `use_tools == false` → `composer_mode="ask"`

