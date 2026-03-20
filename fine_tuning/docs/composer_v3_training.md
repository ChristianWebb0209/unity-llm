# Composer v3 Training and Data Remake

## Goal
Train and ship a Composer model that is strict about tool usage:

- uses only tools that exist in `tools.json` (repo root)
- emits contract-valid tool calls in AGENT mode
- emits exactly one question and no tools in ASK mode
- fails fast in data build and release if contract is violated

---

## What went wrong in v2

v2 improved format consistency versus earlier versions, but we still saw reliability failures at inference.

### 1) Output format drift at inference

In `fine_tuning/testing/results/*_composer_*.json`, we observed responses like:

- bare JSON objects instead of `<tool_call>...</tool_call>`
- multiple JSON objects with noise tokens between them
- malformed wrappers such as `<{"name": "..."}>`
- empty `tool_calls` when answers looked "almost structured"

This is why a compatibility fallback parser had to be added in `rag_service/app/main.py`.

### 2) Unknown/non-runtime tools appeared in responses

Examples included names like:

- `search_internet`
- `create_timer`
- `write_script`
- `get_node_path`
- `export_attribute`

These are not part of the canonical runtime schema in `tools.json` (repo root).

### 3) Metrics were too permissive

Old checks mainly validated structural shape ("is there a tool-like object?"), but did not always enforce:

- strict schema membership
- alias rejection policy
- no-text-outside-XML in AGENT mode

That allowed tool-policy drift to survive until runtime.

### 4) Release gating was not strict enough

There was no hard release blocker that said:

- unknown tools = fail
- malformed calls = fail
- AGENT no-call behavior = fail
- ASK non-question behavior = fail

---

## Important clarification: was v2 "trained stupidly"?

No. The final v2 train set can be schema-clean while production behavior still drifts.

The real problem was not one single bad script. It was an end-to-end contract hardening gap:

- data contract
- canonicalization policy
- validator strictness
- inference contract tests
- release gates

v3 fixes this as a system.

---

## How v3 fixes it

## 1) Frozen v3 tool contract and alias policy

Added:

- `fine_tuning/schemas/composer_v3_tool_contract.json`
- `fine_tuning/schemas/composer_v3_tool_aliases.json`

This gives a versioned source of truth for:

- allowed tool names
- AGENT format rules
- migration aliases (with explicit null/unmappable entries)
- hard rejection policy for unknown/unmappable tools

## 2) Strict validator (hard fail)

Added:

- `fine_tuning/scripts/v3/validate_composer_v3_dataset.py`

Checks AGENT:

- at least one `<tool_call>` block
- parseable inner JSON
- tool name in schema + v3 contract
- `arguments` must be dict
- required keys present
- type checks where schema defines types
- no extra text outside tool blocks (optional `<think>` allowed per contract)
- no legacy alias tool names in final output

Checks ASK:

- no tool blocks
- no `__OPTIONS__`
- single line
- exactly one question mark

## 3) Adversarial negatives for contract pressure

Added:

- `fine_tuning/scripts/v3/generate_composer_v3_adversarial_negatives.py`

Generates ASK-mode examples that bait invalid behavior:

- fake tools
- malformed wrappers
- invalid argument-type requests

Desired output remains one clarifying question, no tools.

## 4) Rebuild AGENT/ASK datasets with strict normalization

Added:

- `fine_tuning/scripts/v3/rebuild_composer_v3_agent_dataset.py`
- `fine_tuning/scripts/v3/rebuild_composer_v3_ask_dataset.py`

AGENT rebuild behavior:

- parse + normalize tool calls
- canonicalize allowed aliases
- reject unmappable aliases
- reject unknown tools
- reject malformed calls
- dedupe records
- emit strict AGENT records only

ASK rebuild behavior:

- enforce one-question-only contract
- reject tool calls or multi-line output
- dedupe records

Outputs:

- `fine_tuning/data/composer_v3/agent_strict.jsonl`
- `fine_tuning/data/composer_v3/ask_strict.jsonl`

## 5) Agent-heavy deterministic mix

Added:

- `fine_tuning/scripts/v3/build_composer_v3_dataset_mix.py`

Builds:

- `fine_tuning/data/composer_v3/train.jsonl`
- `fine_tuning/data/composer_v3/val.jsonl`

Default ratio:

- 90% AGENT / 10% ASK

This biases model behavior toward valid tool execution.

## 6) Audit report (machine + human)

Added:

- `fine_tuning/scripts/v3/audit_composer_v3_dataset.py`

Outputs:

- `fine_tuning/testing/results/composer_v3_data_audit.json`
- `fine_tuning/testing/results/composer_v3_data_audit.md`

Tracks:

- tool frequency
- unknown schema/contract tools
- alias usage
- malformed blocks
- ASK violations

## 7) Pre-train gate (blocking)

Added:

- `fine_tuning/scripts/v3/run_composer_v3_pretrain_gates.py`

Blocks training if any strict validator/audit threshold fails.

## 8) Post-train inference suite and release gate (blocking)

Added:

- `fine_tuning/testing/composer_v3_inference_contract_suite.py`
- `fine_tuning/scripts/v3/run_composer_v3_posttrain_release_gate.py`

Blocks release for:

- unknown tool names
- malformed tool-call structure
- AGENT prompts with no tool calls
- ASK prompts with tools or non-question output

---

## Operational workflow for v3

Run in this order:

1. Rebuild strict datasets:
   - `python fine_tuning/scripts/v3/rebuild_composer_v3_agent_dataset.py`
   - `python fine_tuning/scripts/v3/rebuild_composer_v3_ask_dataset.py`
2. Build train/val mix:
   - `python fine_tuning/scripts/v3/build_composer_v3_dataset_mix.py`
3. Run pre-train gates:
   - `python fine_tuning/scripts/v3/run_composer_v3_pretrain_gates.py`
4. Train v3 adapter.
5. Run post-train release gate:
   - `python fine_tuning/scripts/v3/run_composer_v3_posttrain_release_gate.py`

If any gate fails, do not proceed to next stage.

---

## Success criteria

v3 is considered ready only when all are true:

- unknown tool count is zero in dataset audits
- alias tool count is zero in final train/val
- malformed AGENT contract outputs are zero in dataset validation
- ASK contract violations are zero
- post-train inference contract suite passes end-to-end against the target endpoint

