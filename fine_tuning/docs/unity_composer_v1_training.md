# Unity Composer V1 Training Plan

## What “V1” means here
This training targets the **Unity Editor V1 tool subset**: the exact tools Unity’s editor client can execute deterministically (real executors in V1, no DB/network persistence).

Tool calls are **hard-locked** during dataset rebuild/validation by:
- `tools.json` (repo-root schema)
- `fine_tuning/schemas/composer_v3_tool_contract.json` (the allowed tool set)
- `fine_tuning/schemas/composer_v3_tool_aliases.json` (only safe aliases; unmappable aliases are rejected)

## Core contract (output format)
The dataset uses the Composer V2/Composer V3 formatting contract:

### AGENT mode
- `assistant.content` must contain **one or more** XML blocks:
  - `<tool_call>{"name":"tool_name","arguments":{...}}</tool_call>`
- No extra text outside tool blocks (optionally `<think>...</think>` may appear inside the assistant content).
- Each tool call must be:
  - in the V1 contract tool set
  - present in `tools.json`
  - have a JSON-object `arguments` payload satisfying required keys and basic type checks

### ASK mode
- `assistant.content` must be **exactly one short question**
- Must end with `?`
- Must contain:
  - no `<tool_call>` blocks
  - no `__OPTIONS__`
  - no newlines

## What we will generate

### 1) AGENT examples (tool-using edits + Unity scene ops)
We generate records where the user asks for concrete editor actions. The model is expected to respond in AGENT mode by emitting strict tool calls that fall within the Unity V1 contract:

File edit tools:
- `read_file`
- `create_file`
- `write_file`
- `append_to_file`
- `apply_patch`
- `delete_file`
- `create_script` (C#)

Unity scene tools:
- `open_scene`
- `save_scene`
- `get_scene_hierarchy`
- `create_game_object`
- `delete_game_object`
- `add_component`
- `remove_component`
- `set_component_property`
- `connect_ui_event`
- `collect_compile_errors`
- `run_unity_editor_tests`

### 2) ASK examples (clarifying questions only)
We generate records where the user request is ambiguous and the model should ask one clarifying question:
- missing required editor identifiers (file path, component type, hierarchy path, event target method, etc.)
- uncertain whether to use current open scene vs a specific scene path

### 3) Adversarial negatives (ASK-mode contract bait)
We rely on existing `fine_tuning/scripts/v3/generate_composer_v3_adversarial_negatives.py` to create ASK-mode examples that bait invalid tool usage.

These are used to teach:
- no tool blocks in ASK mode
- strict “one question only” behavior

## How examples are generated tonight (OpenAI-backed, no execution)
We will generate raw candidate examples by calling an OpenAI-compatible chat model (Responses/Chat Completions) using:
- `rag_service/app/prompts.py`:
  - `COMPOSER_V2_SYSTEM_PROMPT_AGENT`
  - `COMPOSER_V2_SYSTEM_PROMPT_ASK`

We instruct the model in the **user prompt** to:
- use **only** the V1 contract tool set
- output strictly the required XML wrapper(s) for AGENT mode
- output exactly one question in ASK mode

We then post-parse and reject any candidate outputs that do not:
- match the XML `<tool_call>...</tool_call>` format
- contain only contract-allowed tool names
- contain argument payloads shaped as JSON objects

Raw outputs are written as JSONL with this standard record shape:
```json
{
  "messages": [
    {"role":"system","content":"<system prompt>"},
    {"role":"user","content":"<user prompt>"},
    {"role":"assistant","content":"<assistant content>"}
  ]
}
```

## Pipeline (rebuild + validation before training)
After generating raw JSONL:
1. Rebuild strict AGENT dataset:
   - `python fine_tuning/scripts/v3/rebuild_composer_v3_agent_dataset.py`
2. Rebuild strict ASK dataset:
   - `python fine_tuning/scripts/v3/rebuild_composer_v3_ask_dataset.py`
3. Validate:
   - `python fine_tuning/scripts/v3/validate_composer_v3_dataset.py`
4. Mix train/val:
   - `python fine_tuning/scripts/v3/build_composer_v3_dataset_mix.py`
5. Gates + release checks:
   - `python fine_tuning/scripts/v3/run_composer_v3_pretrain_gates.py`
   - `python fine_tuning/scripts/v3/run_composer_v3_posttrain_release_gate.py`

## Evaluation checklist (what we will verify)
- Dataset audit shows:
  - zero unknown tools
  - zero alias-tool outputs in final records
  - malformed tool blocks count is 0
- Inference contract suite passes against the configured `/composer/query` endpoint.

