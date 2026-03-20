#!/usr/bin/env python3
"""
Generate raw candidate Unity Composer V1 training data via OpenAI-compatible APIs.

This script does NOT run any training. It only creates JSONL candidate records that
will later be normalized/rebuilt/validated by the existing Composer v3 pipeline:
- rebuild_composer_v3_agent_dataset.py
- rebuild_composer_v3_ask_dataset.py
- validate_composer_v3_dataset.py

Contract hard-lock happens via:
- fine_tuning/schemas/composer_v3_tool_contract.json (allowed tool names)
- tools.json (argument shapes)

Requirements:
- `pip install openai` (or ensure the `openai` package is available)

Output JSONL record format:
{
  "messages": [
    {"role":"system","content": "..."},
    {"role":"user","content": "..."},
    {"role":"assistant","content": "..."}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_JSON = REPO_ROOT / "tools.json"
CONTRACT_JSON = REPO_ROOT / "fine_tuning" / "schemas" / "composer_v3_tool_contract.json"

# Load env defaults from repo .env so callers don't need to pass API keys.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(REPO_ROOT / ".env", override=False)
except Exception:
    # If python-dotenv isn't available, we still rely on existing environment variables.
    pass

# Allow importing local repo packages (rag_service/...) when running from any CWD.
sys.path.insert(0, str(REPO_ROOT))
from rag_service.app.prompts import COMPOSER_V2_SYSTEM_PROMPT_AGENT, COMPOSER_V2_SYSTEM_PROMPT_ASK


_TOOL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", flags=re.DOTALL)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _type_matches(expected_type: str, value: Any) -> bool:
    t = expected_type.lower()
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def _extract_tool_blocks(assistant_content: str) -> List[str]:
    return [s.strip() for s in _TOOL_BLOCK_RE.findall(assistant_content or "")]


def _validate_agent_output(
    assistant_content: str,
    *,
    schema_by_name: Dict[str, Dict[str, Any]],
    contract_tools: Set[str],
    allow_think_blocks: bool,
) -> Tuple[bool, str]:
    s = (assistant_content or "").strip()
    if not s:
        return False, "empty_assistant"

    if "__OPTIONS__" in s:
        return False, "contains___OPTIONS__"

    blocks = _extract_tool_blocks(s)
    if not blocks:
        return False, "missing_tool_call_blocks"

    # Remove tool blocks and optional <think> blocks; leftover must be empty.
    leftover = _TOOL_BLOCK_RE.sub("", s)
    if allow_think_blocks:
        leftover = re.sub(r"<think>.*?</think>", "", leftover, flags=re.DOTALL)
    if leftover.strip():
        return False, "extra_text_outside_tool_calls"

    for inner in blocks:
        try:
            payload = json.loads(inner)
        except Exception:
            return False, "tool_call_inner_json_invalid"
        if not isinstance(payload, dict):
            return False, "tool_call_inner_not_object"
        name = payload.get("name")
        args = payload.get("arguments")
        if not isinstance(name, str) or not name:
            return False, "tool_name_missing"
        if name not in contract_tools:
            return False, f"tool_not_in_contract:{name}"
        if not isinstance(args, dict):
            return False, "arguments_not_object"

        schema = schema_by_name.get(name)
        if not schema:
            return False, f"tool_not_in_schema:{name}"

        params = schema.get("parameters") or {}
        required = params.get("required") or []
        for req in required:
            if req not in args:
                return False, f"missing_required_arg:{name}:{req}"

        props = params.get("properties") or {}
        if isinstance(props, dict):
            for k, v in args.items():
                spec = props.get(k)
                if not isinstance(spec, dict):
                    continue
                t = spec.get("type")
                if t and not _type_matches(str(t), v):
                    return False, f"arg_type_mismatch:{name}:{k}"

    return True, "ok"


def _validate_ask_output(assistant_content: str) -> Tuple[bool, str]:
    s = (assistant_content or "").strip()
    if not s:
        return False, "empty_assistant"
    if "<tool_call>" in s or "</tool_call>" in s:
        return False, "contains_tool_call_blocks"
    if "__OPTIONS__" in s:
        return False, "contains___OPTIONS__"
    if "\n" in s:
        return False, "contains_newlines"
    if not s.endswith("?"):
        return False, "missing_question_mark"
    if s.count("?") != 1:
        return False, "multiple_or_missing_questions"
    return True, "ok"


def _build_prompt_templates() -> List[Tuple[str, str]]:
    """
    Returns list of (tool_name, user_prompt_hint).
    Prompts are intentionally structured to increase format acceptance.
    """

    return [
        (
            "read_file",
            "Read an existing file and emit ONE <tool_call> using read_file only.",
        ),
        (
            "create_file",
            "Create/initialize a file and emit ONE <tool_call> using create_file only.",
        ),
        (
            "create_script",
            "Create a C# script and emit ONE <tool_call> using create_script only.",
        ),
        (
            "apply_patch",
            "Patch an existing C# file and emit ONE <tool_call> using apply_patch only.",
        ),
        (
            "write_file",
            "Create or overwrite a file by writing full content and emit ONE <tool_call> using write_file only.",
        ),
        (
            "append_to_file",
            "Append to a file and emit ONE <tool_call> using append_to_file only.",
        ),
        (
            "delete_file",
            "Delete a file and emit ONE <tool_call> using delete_file only.",
        ),
        (
            "open_scene",
            "Open a Unity scene and emit ONE <tool_call> using open_scene only.",
        ),
        (
            "save_scene",
            "Save the current/active Unity scene and emit ONE <tool_call> using save_scene only.",
        ),
        (
            "get_scene_hierarchy",
            "Snapshot the current scene hierarchy and emit ONE <tool_call> using get_scene_hierarchy only.",
        ),
        (
            "create_game_object",
            "Create a GameObject under /Canvas with a name and emit ONE <tool_call> using create_game_object only.",
        ),
        (
            "delete_game_object",
            "Delete a GameObject by hierarchy path and emit ONE <tool_call> using delete_game_object only.",
        ),
        (
            "add_component",
            "Add a component to an existing GameObject and emit ONE <tool_call> using add_component only.",
        ),
        (
            "remove_component",
            "Remove a component from a GameObject and emit ONE <tool_call> using remove_component only.",
        ),
        (
            "set_component_property",
            "Set a serialized component property and emit ONE <tool_call> using set_component_property only.",
        ),
        (
            "connect_ui_event",
            "Wire a UnityEvent listener via connect_ui_event and emit ONE <tool_call> using connect_ui_event only.",
        ),
        (
            "collect_compile_errors",
            "Collect compile diagnostics via collect_compile_errors and emit ONE <tool_call> using collect_compile_errors only.",
        ),
        (
            "lint_file",
            "Lint a C# script by forcing Unity to recompile it and emit ONE <tool_call> using lint_file only.",
        ),
        (
            "run_unity_editor_tests",
            "Trigger Unity EditMode tests via run_unity_editor_tests and emit ONE <tool_call> using run_unity_editor_tests only.",
        ),
    ]


def _arg_payload_for_tool(tool_name: str, rng: random.Random) -> Dict[str, Any]:
    # Keep args small and schema-safe. Required keys are provided; optional keys are kept generic.
    if tool_name == "create_script":
        class_name = rng.choice(["PlayerController", "HealthController", "EnemyAIController"])
        path = f"Assets/Scripts/{class_name}_{rng.randint(0, 999999)}.cs"
        initial_content = f"\n    public void TickV1() {{ /* TODO {rng.randint(0, 999999)} */ }}\n"
        return {
            "path": path,
            "language": "csharp",
            "extends_class": "MonoBehaviour",
            "initial_content": initial_content,
            "template": "",
        }
    if tool_name == "read_file":
        return {
            "path": rng.choice(
                [
                    "Assets/Scripts/PlayerController.cs",
                    "Assets/Scripts/EnemyAIController.cs",
                    f"Assets/Scripts/GeneratedV1_{rng.randint(0, 999999)}.cs",
                ]
            )
        }
    if tool_name == "create_file":
        path = rng.choice(
            [
                "Assets/Scripts/CreatedByV1.cs",
                "Assets/Scripts/EmptyStubV1.cs",
                f"Assets/Scripts/GeneratedV1_{rng.randint(0, 999999)}.cs",
            ]
        )
        # create_file supports optional content in tools.json; we keep it empty.
        return {"path": path}
    if tool_name == "apply_patch":
        path = rng.choice(
            [
                "Assets/Scripts/PlayerController.cs",
                "Assets/Scripts/EnemyAIController.cs",
                f"Assets/Scripts/PlayerController_{rng.randint(0, 999999)}.cs",
            ]
        )
        var = rng.choice(["velocity", "health", "speed", "damage", "mana"])
        old_string = f"{var} = {rng.randint(0, 500)}f;"
        new_string = f"{var} = {rng.randint(0, 500)}f;"
        return {"path": path, "old_string": old_string, "new_string": new_string}
    if tool_name == "write_file":
        path = f"Assets/Scripts/GeneratedHelper_{rng.randint(0, 999999)}.cs"
        content = (
            "using UnityEngine;\n\n"
            f"public static class TempUtil_{rng.randint(0, 999999)} "
            "{{ public static int Add(int a,int b)=>a+b; }}\n"
        )
        return {"path": path, "content": content}
    if tool_name == "append_to_file":
        path = rng.choice(
            [
                "Assets/Scripts/PlayerController.cs",
                "Assets/Scripts/HealthController.cs",
                f"Assets/Scripts/PlayerController_{rng.randint(0, 999999)}.cs",
            ]
        )
        return {"path": path, "content": f"\n// V1 append line {rng.randint(0, 999999)}\n"}
    if tool_name == "delete_file":
        path = rng.choice(
            [
                "Assets/Scripts/TempUtil.cs",
                "Assets/Scripts/OldEnemyAIController.cs",
                f"Assets/Scripts/TempUtil_{rng.randint(0, 999999)}.cs",
            ]
        )
        return {"path": path}
    if tool_name == "open_scene":
        scene_path = rng.choice(
            [
                "Assets/Scenes/Main.unity",
                "Assets/Scenes/Sample.unity",
                f"Assets/Scenes/GeneratedScene_{rng.randint(0, 999999)}.unity",
            ]
        )
        open_mode = rng.choice(["Single", "Additive"])
        return {"scene_path": scene_path, "open_mode": open_mode}
    if tool_name == "save_scene":
        # Save-scene args are optional; add variety while staying schema-safe.
        return {
            "save_as_copy": bool(rng.randint(0, 1)),
            "scene_path": f"Assets/Scenes/SavedV1_{rng.randint(0, 999999)}.unity",
        }
    if tool_name == "get_scene_hierarchy":
        return {
            "scene_path": "",
            "include_inactive": True,
            "max_nodes": int(rng.randint(256, 8000)),
        }
    if tool_name == "create_game_object":
        return {
            "parent_path": "/Canvas",
            "name": rng.choice(["V1Button", "V1Panel", "V1Marker"]) + f"_{rng.randint(0, 999999)}",
        }
    if tool_name == "delete_game_object":
        return {
            "game_object_path": rng.choice(["/Canvas/V1Button", "/Canvas/V1Panel"]) + f"_{rng.randint(0, 999999)}",
        }
    if tool_name == "add_component":
        return {
            "game_object_path": rng.choice(["/Canvas/V1Button", "/Canvas/V1Panel"]) + f"_{rng.randint(0, 999999)}",
            "component_type": rng.choice(
                [
                    "UnityEngine.UI.Button",
                    "UnityEngine.BoxCollider",
                    "UnityEngine.UI.Text",
                    "UnityEngine.Rigidbody",
                    "UnityEngine.Animator",
                ]
            ),
        }
    if tool_name == "remove_component":
        return {
            "game_object_path": rng.choice(["/Canvas/V1Button", "/Canvas/V1Panel"]) + f"_{rng.randint(0, 999999)}",
            "component_type": rng.choice(["UnityEngine.BoxCollider", "UnityEngine.UI.Text", "UnityEngine.UI.Button"]),
            "component_index": int(rng.randint(0, 5)),
        }
    if tool_name == "set_component_property":
        return {
            "game_object_path": rng.choice(["/Canvas/V1Button", "/Canvas/V1Panel"]) + f"_{rng.randint(0, 999999)}",
            "component_type": "UnityEngine.UI.Text",
            "property_path": rng.choice(["m_Text", "m_Name"]),
            "value": rng.choice(["Hello V1", "Updated Text", "V1"]) + f"_{rng.randint(0, 999999)}",
        }
    if tool_name == "connect_ui_event":
        return {
            "source_game_object_path": "/Canvas/V1Button_" + str(rng.randint(0, 999999)),
            "component_type": "UnityEngine.UI.Button",
            "event_property_path": "m_OnClick",
            "target_game_object_path": "/Canvas/V1Panel_" + str(rng.randint(0, 999999)),
            "target_method_name": rng.choice(["OnPressed", "OnClicked", "HandleButton"]) + f"_{rng.randint(0, 999999)}",
            "mode": rng.choice(["dynamic", "static"]),
        }
    if tool_name == "collect_compile_errors":
        return {"include_warnings": True, "max_items": int(rng.randint(50, 800))}
    if tool_name == "lint_file":
        return {
            "path": rng.choice(
                ["Assets/Scripts/PlayerController.cs", "Assets/Scripts/EnemyAIController.cs", f"Assets/Scripts/LintV1_{rng.randint(0, 999999)}.cs"]
            ),
            "include_warnings": True,
            "max_items": int(rng.randint(50, 800)),
        }
    if tool_name == "run_unity_editor_tests":
        assembly_pool = ["Assembly-CSharp", "UnityLLM.Tests", "EditorTests"]
        test_pool = ["TestConnectUIEvent", "TestApplyPatch", "TestLintFile", "TestWriteFile"]
        k_asm = int(rng.randint(0, 2))
        k_tests = int(rng.randint(0, 3))
        assembly_names = rng.sample(assembly_pool, k=k_asm)
        test_names = rng.sample(test_pool, k=k_tests)
        return {
            "test_mode": "EditMode",
            "assembly_names": assembly_names,
            "test_names": test_names,
            "run_synchronously": bool(rng.randint(0, 1)),
        }

    return {}


def _build_agent_user_prompt(
    tool_name: str,
    args_obj: Dict[str, Any],
    allowed_tools: Sequence[str],
    *,
    extra_hint: str,
) -> str:
    allowed = ", ".join(sorted(allowed_tools))
    return (
        f"You are in AGENT mode. Use ONLY the following tools: [{allowed}].\n"
        f"{tool_name} should be the ONLY tool call in your reply.\n\n"
        f"Emit exactly ONE XML tool block with this structure:\n"
        f"<tool_call>{{\"name\":\"{tool_name}\",\"arguments\":<ARG_OBJECT>}}</tool_call>\n\n"
        f"Use this ARG_OBJECT exactly (arguments must be a JSON object):\n"
        f"{json.dumps(args_obj, ensure_ascii=False)}\n\n"
        f"User request: Perform the editor action implied by these arguments.\n"
        f"Extra hint: {extra_hint}"
    )


def _build_ask_user_prompt(missing_kind: str, rng: random.Random) -> str:
    # Keep prompts short; ask-model is required to respond with exactly one question ending in '?'.
    examples = {
        "file_path": f"Fix my script but I did not specify a file path. I want it to change behavior: {rng.choice(['add logging','rename a method','set a new default value'])}.",
        "scene_path": "Open the right scene and create the UI objects, but tell me which scene path to use.",
        "event_target": "Wire a Button click handler, but I’m missing which target component/method to call.",
        "component_prop": "Set a UI text property, but I did not provide the exact property_path for the serialized field.",
    }
    return examples[missing_kind]


def _build_synthetic_ask_answer(missing_kind: str, rng: random.Random) -> str:
    # Must be a single-line question ending with '?' and contain exactly one '?'.
    # The ask-mode validator enforces formatting, not semantic correctness.
    templates: Dict[str, List[str]] = {
        "file_path": [
            "What exact file path under Assets should I use?",
            "Which exact file path under Assets should I edit?",
        ],
        "scene_path": [
            "Which scene path should I open to perform this UI change?",
            "What is the correct Assets scene path to use?",
        ],
        "event_target": [
            "What source game_object_path and event_property_path should I use?",
            "Which UI event source path and event property should I wire?",
        ],
        "component_prop": [
            "What property_path should I set for the serialized field?",
            "Which exact property_path should be used for the component value?",
        ],
    }
    options = templates.get(missing_kind) or templates["file_path"]
    s = rng.choice(options)
    # Ensure exactly one '?' for the validator, and make it unique for scaling.
    s = s.strip()
    if s.endswith("?"):
        base = s[:-1].rstrip()
        s = f"{base} (id {rng.randint(0, 999999)})?"
    else:
        s = s.replace("?", "") + f" (id {rng.randint(0, 999999)})?"
    return s.strip()


def _openai_chat_completion(
    *,
    api_key: str,
    base_url: Optional[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    request_timeout_seconds: int,
) -> str:
    from openai import OpenAI  # imported lazily

    client = OpenAI(api_key=api_key, base_url=base_url or None)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=request_timeout_seconds,
    )
    return resp.choices[0].message.content or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Unity Composer V1 candidate datasets via OpenAI-compatible API")
    parser.add_argument("--mode", choices=["agent", "ask", "both"], default="both")
    parser.add_argument("--count-agent", type=int, default=200)
    parser.add_argument("--count-ask", type=int, default=80)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output JSONL files instead of appending.",
    )
    parser.add_argument(
        "--strict-contract-coverage",
        action="store_true",
        help="Fail if the contract contains tools that we cannot generate from prompt templates.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate valid outputs without calling OpenAI (fast local generation).",
    )
    parser.add_argument("--api-key", type=str, default=os.getenv("OPENAI_API_KEY", "").strip())
    parser.add_argument("--base-url", type=str, default=os.getenv("OPENAI_BASE_URL", "").strip() or None)
    parser.add_argument("--model", type=str, default=os.getenv("OPENAI_MODEL", "").strip() or os.getenv("COMPOSER_MODEL", "").strip() or "gpt-4.1-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--output-agent-jsonl", type=str, default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "unity_v1_agent_candidates.jsonl"))
    parser.add_argument("--output-ask-jsonl", type=str, default=str(REPO_ROOT / "fine_tuning" / "data" / "composer_v3" / "unity_v1_ask_candidates.jsonl"))
    args = parser.parse_args()
    if not args.synthetic and not args.api_key:
        raise SystemExit("Missing --api-key (or set OPENAI_API_KEY), or use --synthetic.")

    rng = random.Random(args.seed)

    tools_schema = _load_json(TOOLS_JSON)
    schema_by_name = {t["name"]: t for t in tools_schema if isinstance(t, dict) and t.get("name")}
    contract_doc = _load_json(CONTRACT_JSON)
    contract_tools = set(contract_doc.get("tools") or [])
    allow_think_blocks = bool((contract_doc.get("agent_format") or {}).get("allow_think_blocks", False))

    # Ensure we only generate for contract tools we actually validated.
    prompt_templates = _build_prompt_templates()
    agent_tools = [t for t, _ in prompt_templates if t in contract_tools]
    if not agent_tools:
        raise SystemExit("No agent prompt templates match the contract tool set.")

    # One more schema check: contract tools should be covered by prompt templates.
    prompt_tools = {t for t, _ in prompt_templates}
    missing_contract_tools = sorted(contract_tools - prompt_tools)
    if missing_contract_tools:
        print(
            f"[schema-preflight] Contract tools missing from prompt templates: {len(missing_contract_tools)}\n"
            + "\n".join(missing_contract_tools)
        )
        if args.strict_contract_coverage:
            raise SystemExit(2)

    output_agent = Path(args.output_agent_jsonl)
    output_ask = Path(args.output_ask_jsonl)
    output_agent.parent.mkdir(parents=True, exist_ok=True)
    output_ask.parent.mkdir(parents=True, exist_ok=True)

    # We'll append by default, but allow overwrite for deterministic remakes.
    agent_out_mode = "w" if args.overwrite else "a"
    ask_out_mode = "w" if args.overwrite else "a"
    agent_out_f = output_agent.open(agent_out_mode, encoding="utf-8") if args.mode in ("agent", "both") else None
    ask_out_f = output_ask.open(ask_out_mode, encoding="utf-8") if args.mode in ("ask", "both") else None

    def write_record(out_f: Any, system_prompt: str, user_prompt: str, assistant_content: str) -> None:
        out_f.write(
            json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": assistant_content},
                    ]
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        out_f.flush()

    # AGENT generation
    if args.mode in ("agent", "both"):
        written = 0
        attempts = 0
        while written < args.count_agent:
            attempts += 1
            if attempts > args.count_agent * (args.max_retries + 1) * 5:
                raise SystemExit("Too many attempts generating agent records; check model/provider.")

            tool_name = rng.choice(agent_tools)
            args_obj = _arg_payload_for_tool(tool_name, rng)
            if not isinstance(args_obj, dict):
                args_obj = {}
            extra_hint = rng.choice(
                [
                    "Keep the change minimal and V1-compatible.",
                    "Prefer the smallest safe edit and no extra commentary.",
                    "Assume the editor will apply the tool call exactly as requested.",
                    "Do not ask questions in agent mode; only emit tool calls.",
                    "Use the provided arguments precisely; do not invent missing fields.",
                ]
            )
            user_prompt = _build_agent_user_prompt(
                tool_name,
                args_obj,
                allowed_tools=sorted(contract_tools),
                extra_hint=extra_hint,
            )
            if args.synthetic:
                inner = json.dumps(
                    {"name": tool_name, "arguments": args_obj},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                assistant_content = f"<tool_call>{inner}</tool_call>"
                ok, _reason = _validate_agent_output(
                    assistant_content,
                    schema_by_name=schema_by_name,
                    contract_tools=contract_tools,
                    allow_think_blocks=allow_think_blocks,
                )
                if not ok:
                    continue
                write_record(agent_out_f, COMPOSER_V2_SYSTEM_PROMPT_AGENT, user_prompt, assistant_content)
                written += 1
            else:
                assistant_content = ""
                last_err = ""
                for _ in range(args.max_retries):
                    try:
                        assistant_content = _openai_chat_completion(
                            api_key=args.api_key,
                            base_url=args.base_url,
                            model=args.model,
                            system_prompt=COMPOSER_V2_SYSTEM_PROMPT_AGENT,
                            user_prompt=user_prompt,
                            temperature=args.temperature,
                            max_tokens=args.max_tokens,
                            request_timeout_seconds=args.request_timeout_seconds,
                        )
                        ok, reason = _validate_agent_output(
                            assistant_content,
                            schema_by_name=schema_by_name,
                            contract_tools=contract_tools,
                            allow_think_blocks=allow_think_blocks,
                        )
                        if ok:
                            write_record(agent_out_f, COMPOSER_V2_SYSTEM_PROMPT_AGENT, user_prompt, assistant_content)
                            written += 1
                            break
                        last_err = reason
                        time.sleep(0.25)
                    except Exception as e:
                        last_err = f"openai_exception:{type(e).__name__}:{e}"
                        time.sleep(0.5)

                if last_err and not assistant_content:
                    continue
            if written % 25 == 0 and written > 0:
                print(f"[agent] written={written}/{args.count_agent}")

    # ASK generation
    if args.mode in ("ask", "both"):
        missing_kinds = ["file_path", "scene_path", "event_target", "component_prop"]
        written = 0
        attempts = 0
        while written < args.count_ask:
            attempts += 1
            if attempts > args.count_ask * (args.max_retries + 1) * 5:
                raise SystemExit("Too many attempts generating ask records; check model/provider.")

            missing_kind = rng.choice(missing_kinds)
            user_prompt = _build_ask_user_prompt(missing_kind, rng)
            if args.synthetic:
                assistant_content = _build_synthetic_ask_answer(missing_kind, rng)
                ok, _reason = _validate_ask_output(assistant_content)
                if not ok:
                    continue
                write_record(ask_out_f, COMPOSER_V2_SYSTEM_PROMPT_ASK, user_prompt, assistant_content)
                written += 1
            else:
                assistant_content = ""
                last_err = ""
                for _ in range(args.max_retries):
                    try:
                        assistant_content = _openai_chat_completion(
                            api_key=args.api_key,
                            base_url=args.base_url,
                            model=args.model,
                            system_prompt=COMPOSER_V2_SYSTEM_PROMPT_ASK,
                            user_prompt=user_prompt,
                            temperature=args.temperature,
                            max_tokens=args.max_tokens,
                            request_timeout_seconds=args.request_timeout_seconds,
                        )
                        ok, reason = _validate_ask_output(assistant_content)
                        if ok:
                            write_record(ask_out_f, COMPOSER_V2_SYSTEM_PROMPT_ASK, user_prompt, assistant_content)
                            written += 1
                            break
                        last_err = reason
                        time.sleep(0.25)
                    except Exception as e:
                        last_err = f"openai_exception:{type(e).__name__}:{e}"
                        time.sleep(0.5)

            if written % 25 == 0 and written > 0:
                print(f"[ask] written={written}/{args.count_ask}")

    if agent_out_f:
        agent_out_f.close()
    if ask_out_f:
        ask_out_f.close()

    print("Generation complete.")


if __name__ == "__main__":
    main()

