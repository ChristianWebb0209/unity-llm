import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import re
from typing import Any, Dict, List, Optional, Tuple, Literal
from typing import TypedDict

from dotenv import load_dotenv
from openai import OpenAI
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from .services.repo_indexing import (
    get_inbound_refs,
    get_most_referenced_res_paths,
    get_repo_index_stats,
    list_indexed_paths,
)
from .tools.deps import UnityQueryDeps
from .tools import (
    create_unity_agent,
    dispatch_tool_call,
    get_openai_tools_payload,
    get_registered_tools,
)
from .services.context.context_builder import (
    build_context_usage,
    build_current_scene_scripts_context,
    build_ordered_blocks,
    build_related_files_context,
    blocks_to_user_content,
    extract_extends_from_script,
    get_context_limit,
    list_project_files,
    read_project_file,
    trim_text_to_tokens,
)
from .services.context import (
    append_project_file,
    apply_project_patch,
    apply_project_patch_unified,
    build_conversation_context,
    grep_project_files,
    list_project_directory,
    read_project_unity_ini,
    search_project_files,
    write_project_file,
)
from .services.context.viewer import build_context_view
from .services.context.openviking_context import (
    add_turn_and_commit as openviking_add_turn_and_commit,
    ensure_openviking_data_dir,
    find_memories as openviking_find_memories,
)
from .services.console_service import dim as _dim, cyan as _cyan, green as _green, yellow as _yellow
from .prompts import (
    COMPOSER_SYSTEM_PROMPT,
    COMPOSER_V2_SYSTEM_PROMPT_AGENT,
    COMPOSER_V2_SYSTEM_PROMPT_ASK,
    UNITY_AGENT_SYSTEM_PROMPT,
)


# Load shared root env first, then service-local overrides.
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(_repo_root, ".env"))
load_dotenv(os.path.join(_repo_root, "rag_service", ".env"), override=True)

# Only show WARNING and above for watchfiles/reload — avoid info spam when files change
for _watch_log in ("watchfiles", "watchfiles.main", "uvicorn.reload"):
    logging.getLogger(_watch_log).setLevel(logging.WARNING)


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Suppress noisy CancelledError tracebacks on Ctrl+C shutdown."""
    exc = context.get("exception")
    if isinstance(exc, asyncio.CancelledError):
        return
    loop.default_exception_handler(context)


class _SuppressCancelledErrorFilter(logging.Filter):
    """Filter out ERROR logs for asyncio.CancelledError (clean Ctrl+C shutdown)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        if record.exc_info and record.exc_info[0] is not None:
            if record.exc_info[0] is asyncio.CancelledError:
                return False
        if "CancelledError" in (record.getMessage() or ""):
            return False
        return True


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_asyncio_exception_handler)
    # Suppress ERROR-level tracebacks for CancelledError on shutdown (Ctrl+C)
    for name in ("uvicorn", "uvicorn.error", "starlette.routing", ""):
        log = logging.getLogger(name) if name else logging.root
        log.addFilter(_SuppressCancelledErrorFilter())
    try:
        ensure_openviking_data_dir()
        yield
    except asyncio.CancelledError:
        pass
    finally:
        pass


app = FastAPI(title="Unity RAG Service", version="0.1.0", lifespan=lifespan)
# NOTE: The Unity plugin is responsible for persisting state (usage, edit history, lint repair memory, and repo proximity).
# To keep this backend stateless, we do not initialize server-side persistence at startup.


class SourceChunk(TypedDict, total=False):
    """
    Minimal snippet metadata used in responses.

    NOTE: Older versions of this service used Chroma/Supabase-backed RAG retrieval
    (see deprecated rag_core.py). That path is now removed; this model remains only
    to preserve the response shape for 'snippets'.
    """

    id: str
    source_path: str
    score: float
    text_preview: str
    metadata: Dict[str, Any]


_openai_client: Optional[OpenAI] = None


# Approximate pricing for OpenAI models (USD per 1K tokens).
# Values taken from OpenAI pricing for gpt-4.1-mini:
# - $0.40 per 1M input tokens  => 0.0004 per 1K
# - $1.60 per 1M output tokens => 0.0016 per 1K
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4.1-mini": {
        "input_per_1k": 0.0004,
        "output_per_1k": 0.0016,
    },
}


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0
    input_cost = (prompt_tokens / 1000.0) * pricing["input_per_1k"]
    output_cost = (completion_tokens / 1000.0) * pricing["output_per_1k"]
    return input_cost + output_cost


def _log_usage_and_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    context: str,
) -> None:
    total_tokens = prompt_tokens + completion_tokens
    cost = _estimate_cost_usd(model, prompt_tokens, completion_tokens)
    print(
        _cyan("usage")
        + " "
        + _dim(f"model={model} in={prompt_tokens} out={completion_tokens} total={total_tokens} ${cost:.4f}")
    )


def _log_llm_input(model: str, context: str, input_payload: Any) -> None:
    """Log a one-line summary of the LLM request. Set DEBUG_LLM_INPUT=1 to dump full payload."""
    if os.getenv("DEBUG_LLM_INPUT"):
        try:
            dumped = json.dumps(input_payload, ensure_ascii=False, indent=2)
        except Exception:
            dumped = str(input_payload)
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = dumped.encode(enc, errors="backslashreplace").decode(enc, errors="ignore")
        print(f"{_yellow('llm_input')} context={context} model={model}\n{safe}\n")
        return
    n_msgs = len(input_payload) if isinstance(input_payload, list) else 0
    total_chars = 0
    if isinstance(input_payload, list):
        for m in input_payload:
            if isinstance(m, dict) and "content" in m:
                c = m.get("content")
                total_chars += len(str(c)) if c else 0
    # Keep this line strictly ASCII so Windows consoles don't crash on encode.
    print(_dim(f"llm request model={model} context={context} messages={n_msgs} chars~{total_chars}"))


def _log_rag_request(method_label: str, client_host: str, question: str, color_fn: Any = _green) -> None:
    q = (question.strip() or "")[:56]
    if len(question.strip()) > 56:
        q += "…"
    print(color_fn(method_label) + " " + _dim(f"{client_host} ") + _dim(f"{q!r}"))


def get_openai_client() -> Optional[OpenAI]:
    """
    Lazily create an OpenAI client using environment variables.
    Returns None if no OPENAI_API_KEY is configured.
    """
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("OPENAI_BASE_URL")
    _openai_client = OpenAI(api_key=api_key, base_url=base_url or None)
    return _openai_client


def _openai_client_and_model(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[Optional[OpenAI], str]:
    """
    Return (client, model) for LLM calls. Uses request overrides if provided,
    otherwise env. model is always a non-empty string.
    """
    default_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    if api_key:
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        return client, model or default_model
    client = get_openai_client()
    return client, model or default_model


class QueryContext(TypedDict, total=False):
    engine_version: Optional[str]
    # Preferred script language for answers, based on the active file.
    language: Optional[str]  # "gdscript" | "csharp"
    selected_node_type: Optional[str]
    current_script: Optional[str]
    extra: Dict[str, Any]


class QueryRequest(TypedDict, total=False):
    question: str
    context: Optional[QueryContext]
    top_k: int  # default 8 when omitted
    max_tool_rounds: Optional[int]  # default 5 when None; max tool-call rounds per request
    # Optional overrides from plugin settings (take precedence over env).
    api_key: Optional[str]
    model: Optional[str]
    base_url: Optional[str]
    # Composer v2 mode contract. When omitted, defaults to "agent".
    composer_mode: Optional[Literal["agent", "ask"]]


class QueryResponse(TypedDict, total=False):
    answer: str
    snippets: List[SourceChunk]
    context_usage: Optional[Dict[str, Any]]


class ToolCallResult(TypedDict, total=False):
    tool_name: str
    arguments: Dict[str, Any]
    output: Any


class QueryResponseWithTools(QueryResponse):
    # Optional structured record of any tools the model asked us to run.
    tool_calls: List[ToolCallResult]


def _parse_composer_response(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parse Unity Composer (fine-tuned) model output using the Composer v2 XML tool-call contract.

    Tool calls are expressed as one or more XML blocks:
      <tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>

    Returns (answer_text_without_tool_blocks, raw_tool_calls)
    """
    content = (content or "").strip()
    if not content:
        return "", []

    tool_calls: List[Dict[str, Any]] = []

    # Extract all <tool_call>...</tool_call> blocks.
    # The inner content should be JSON with keys {name, arguments}.
    block_pattern = r"<tool_call>\s*(.*?)\s*</tool_call>"
    for inner in re.findall(block_pattern, content, flags=re.DOTALL):
        inner_str = inner.strip()
        if not inner_str:
            continue
        try:
            payload = json.loads(inner_str)
        except json.JSONDecodeError:
            continue

        name = payload.get("name")
        args = payload.get("arguments") or {}
        if not name:
            continue
        if not isinstance(args, dict):
            args = {}
        tool_calls.append({"name": str(name), "arguments": args})

    # Remove tool blocks from the displayed answer.
    answer = re.sub(block_pattern, "", content, flags=re.DOTALL).strip()
    # Strip <think> blocks from user-visible text.
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

    # Compatibility fallback:
    # Some hosted adapters emit a bare JSON tool-call object/array instead of
    # XML blocks. Accept that shape so /composer/query still returns tool_calls.
    if not tool_calls and answer:
        try:
            parsed = json.loads(answer)
            payloads = parsed if isinstance(parsed, list) else [parsed]
            for p in payloads:
                if not isinstance(p, dict):
                    continue
                name = p.get("name")
                args = p.get("arguments") or {}
                if not name:
                    continue
                if not isinstance(args, dict):
                    args = {}
                tool_calls.append({"name": str(name), "arguments": args})
            if tool_calls:
                answer = ""
        except json.JSONDecodeError:
            pass

    return answer, tool_calls


def _extract_tool_calls_from_agent_result(result: Any) -> List[ToolCallResult]:
    """
    Build List[ToolCallResult] from an agent run result by walking all_messages()
    and pairing ToolCallPart with ToolReturnPart in order.
    """
    out: List[ToolCallResult] = []
    try:
        messages = result.all_messages()
    except Exception:
        return out
    call_parts: List[Tuple[str, Dict[str, Any]]] = []  # (tool_name, args)
    return_contents: List[Any] = []  # output per tool
    for msg in messages:
        parts = getattr(msg, "parts", [])
        for part in parts:
            pname = type(part).__name__
            if pname == "ToolCallPart":
                name = getattr(part, "tool_name", None)
                args = getattr(part, "args", None)
                if name is not None:
                    call_parts.append((str(name), args if isinstance(args, dict) else {}))
            elif pname == "ToolReturnPart":
                content = getattr(part, "content", None)
                return_contents.append(content)
    for i, (name, args) in enumerate(call_parts):
        output = return_contents[i] if i < len(return_contents) else None
        out.append({"tool_name": name, "arguments": args, "output": output})
    return out


def _call_llm_with_rag(
    question: str,
    context_language: Optional[str],
    docs: List[SourceChunk],
    code_snippets: List[SourceChunk],
    is_obscure: bool,
    client: Optional[OpenAI] = None,
    model: Optional[str] = None,
) -> str:
    """
    Call OpenAI chat completions to synthesize an answer from retrieved docs/code.
    Falls back to a verbose plain-text template if no API key is configured.
    """
    if client is None:
        client = get_openai_client()
    if model is None:
        model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    # Build a verbose reasoning-oriented answer if no LLM is available.
    if client is None:
        lines: List[str] = []
        lines.append("This answer is grounded in your Unity docs and project code.\n")
        lines.append(f"Question: {question}\n")
        if context_language:
            lines.append(f"Preferred language: {context_language}\n")
        if docs:
            lines.append("\nRelevant documentation snippets:\n")
            for d in docs:
                lines.append(f"- {d.source_path}")
        if code_snippets:
            lines.append("\nRelevant project code snippets (ordered by relevance/importance):\n")
            for s in code_snippets:
                tags = s.metadata.get("tags", [])
                importance = s.metadata.get("importance", 0.0)
                lines.append(
                    f"- {s.source_path} (importance={importance}, tags={tags})"
                )
        if is_obscure:
            lines.append(
                "\nNote: This appears to be a more niche area of your codebase, "
                "so lower-importance snippets were also considered."
            )
        return "\n".join(lines)

    # Build structured context for the LLM.
    docs_block_lines: List[str] = []
    for d in docs:
        docs_block_lines.append(
            "Official docs snippet from the Unity 4.x manual:\n"
            f"[DOC] path={d.source_path} meta={d.metadata}\n{d.text_preview}\n"
        )
    code_block_lines: List[str] = []
    for s in code_snippets:
        code_block_lines.append(
            "Example project code snippet (not canonical API, use as inspiration only):\n"
            f"[CODE] path={s.source_path} meta={s.metadata}\n{s.text_preview}\n"
        )

    system_prompt = (
        "You are a Unity 4.x development assistant. "
        "You receive a user question plus retrieved documentation and real project code. "
        "Documentation snippets are the authoritative source for engine behavior and APIs. "
        "Example project code snippets are patterns/inspiration only and may reference project-specific types/addons/paths. "
        "Treat example snippets as non-canonical guidance: do not assume they exist in the user's project. "
        "Use ONLY the provided context to answer. Prefer documentation when there is any "
        "conflict between docs and project code. Prefer higher-importance code snippets "
        "when multiple examples are relevant, but you may also rely on lower-importance "
        "snippets if the topic appears niche or under-documented. "
        "Always be explicit about your reasoning: explain which snippets you used "
        "and why, referencing them by their path. "
        "When writing code examples, default to the user's preferred language if given."
    )

    user_prompt_lines: List[str] = []
    user_prompt_lines.append(f"Question: {question}\n")
    if context_language:
        user_prompt_lines.append(f"Preferred language: {context_language}\n")
    if is_obscure:
        user_prompt_lines.append(
            "Heuristic: This seems like a more obscure area of the codebase; "
            "lower-importance snippets may also be relevant.\n"
        )
    if docs_block_lines:
        user_prompt_lines.append("\n=== Documentation Context ===\n")
        user_prompt_lines.extend(docs_block_lines)
    if code_block_lines:
        user_prompt_lines.append("\n=== Project Code Context ===\n")
        user_prompt_lines.extend(code_block_lines)
    user_prompt_lines.append(
        "\nPlease respond with:\n"
        "1) A concise answer.\n"
        "2) A short 'Reasoning' section explaining which docs/code you used and why.\n"
        "3) Code examples in the preferred language if applicable.\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_prompt_lines)},
    ]

    _log_llm_input(model=model, context="rag_answer", input_payload=messages)

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
    )

    # Log token usage and estimated cost if available.
    usage = getattr(completion, "usage", None)
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        _log_usage_and_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            context="rag_answer",
        )
        # Usage persistence is handled client-side in the Unity plugin.

    return completion.choices[0].message.content or ""


def _run_query_with_tools(
    question: str,
    context_language: Optional[str],
    request_context: Optional["QueryContext"],
    top_k: int,
    max_tool_rounds: int = 5,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Tuple[str, List[SourceChunk], List[ToolCallResult], Dict[str, Any]]:
    """
    Orchestrate a full query using:
      - Initial RAG retrieval for docs + code.
      - OpenAI tool calls for follow-up operations (searching again, etc.).

    Returns (final_answer, snippets_used, tool_calls_run, context_usage).
    """
    client, model = _openai_client_and_model(
        api_key=api_key, base_url=base_url, model=model_override
    )
    # If there is no LLM, return a short message (no RAG).
    if client is None:
        usage_obj = build_context_usage(model, [question])
        return (
            "OpenAI client not configured. Set OPENAI_API_KEY (or pass api_key in the request) to use the assistant.",
            [],
            [],
            {
                "model": usage_obj.model,
                "limit_tokens": usage_obj.limit_tokens,
                "estimated_prompt_tokens": usage_obj.estimated_prompt_tokens,
                "percent": 0.0,
            },
        )

    # --- Context builder: ordered blocks + budgets (no docs/code RAG) ---
    # (Agent system instructions live in app.prompts.)

    # Extract active file info from request context (sent by the Unity editor).
    active_file_path = None
    active_file_text = None
    active_scene_path: Optional[str] = None
    scene_root_class: Optional[str] = None
    scene_dimension: Optional[str] = None
    scene_tree: Optional[str] = None
    errors_text = None
    selected_node_type: Optional[str] = None
    if request_context is not None:
        active_file_path = request_context.get("current_script") or None
        extra = request_context.get("extra") or {}
        active_file_text = extra.get("active_file_text") or None
        active_scene_path = (extra.get("active_scene_path") or "").strip() or None
        scene_root_class = (extra.get("scene_root_class") or "").strip() or None
        scene_dimension = (extra.get("scene_dimension") or "").strip().lower() or None
        scene_tree = (extra.get("scene_tree") or "").strip() or None
        errors_text = extra.get("errors_text") or extra.get("lint_output") or None
        project_root_abs = extra.get("project_root_abs") or None
        engine_version = request_context.get("engine_version") or None
        selected_node_type = (request_context.get("selected_node_type") or "").strip() or None
        exclude_block_keys_raw = extra.get("exclude_block_keys")
        exclude_block_keys = (
            list(exclude_block_keys_raw)
            if isinstance(exclude_block_keys_raw, (list, tuple))
            else []
        )
    else:
        extra = {}
        project_root_abs = None
        engine_version = None
        exclude_block_keys = []

    chat_id: Optional[str] = (extra or {}).get("chat_id") if isinstance((extra or {}).get("chat_id"), str) else None

    # If plugin didn't send file text (or it's empty), read from disk.
    if project_root_abs and active_file_path and (not active_file_text or len(active_file_text) < 5):
        disk_text = read_project_file(project_root_abs, active_file_path)
        if disk_text:
            active_file_text = disk_text

    related_files: List[Tuple[str, str]] = []
    if project_root_abs and active_file_path and active_file_text:
        provided_related_res_paths = None
        try:
            if isinstance(extra, dict):
                provided_related_res_paths = extra.get("related_res_paths")
        except Exception:
            provided_related_res_paths = None

        if isinstance(provided_related_res_paths, list) and len(provided_related_res_paths) > 0:
            # Plugin computes one-hop structural proximity client-side.
            # We only need to read the provided Assets/ paths and embed their text.
            max_files = 4
            for p in provided_related_res_paths[:max_files]:
                p_str = str(p).strip()
                if not p_str or p_str == active_file_path:
                    continue
                content = read_project_file(project_root_abs, p_str)
                if content:
                    related_files.append((p_str, content))
        else:
            # Fallback: server-side structural proximity using local JSON snapshot indexing.
            related_files = build_related_files_context(
                project_root_abs=project_root_abs,
                active_file_res_path=active_file_path,
                active_file_text=active_file_text,
                max_files=4,
            )
    # Project core (most-referenced) is omitted in stateless mode.

    # Current scene scripts: parse open scene .tscn and attach all scripts in that scene (aggressive context).
    current_scene_scripts: List[Tuple[str, str]] = []
    if project_root_abs and active_scene_path and active_scene_path.strip().endswith((".tscn", ".scn")):
        try:
            current_scene_scripts = build_current_scene_scripts_context(
                project_root_abs=project_root_abs,
                scene_res_path=active_scene_path.strip(),
                max_scripts=12,
                max_tokens_per_script=1200,
                exclude_path=active_file_path,
            )
        except Exception:
            pass

    # Recent edits working set (agent-time context).
    # Unity plugin can send `context.extra.recent_edits` as a list of strings.
    recent_edits_text: List[str] = []
    try:
        recent_raw = (extra or {}).get("recent_edits") if isinstance(extra, dict) else None
        if isinstance(recent_raw, list) and recent_raw:
            max_items = 12
            for item in recent_raw[:max_items]:
                if isinstance(item, str):
                    s = item.strip()
                    if s:
                        recent_edits_text.append(s)
                elif isinstance(item, dict):
                    # Best-effort formatting for future-proofing; Unity currently sends strings.
                    path = (item.get("path") or item.get("file_path") or item.get("file") or "").strip()
                    tool = (item.get("tool") or item.get("tool_name") or item.get("action") or "").strip()
                    old_txt = item.get("old") or item.get("old_content") or item.get("before") or ""
                    new_txt = item.get("new") or item.get("new_content") or item.get("after") or ""
                    entry = (
                        f"--- Recent edit ({tool or 'edit'}) ---\n"
                        f"Path: {path or '(unknown)'}\n"
                        f"Old:\n{str(old_txt)[:2000]}\n"
                        f"New:\n{str(new_txt)[:2000]}"
                    ).strip()
                    if entry:
                        recent_edits_text.append(entry)
    except Exception:
        # Best-effort only; context window will still work without recent_edits.
        recent_edits_text = []

    # Build dedicated ENVIRONMENT block (high priority, never dropped).
    environment_parts: List[str] = []
    # Context legend: so the LLM knows what it's dealing with (user's project vs reference).
    environment_parts.append(
        "CONTEXT SOURCES: "
        "'Active file' = the file currently focused in the Unity editor (user's project, Assets/ path). "
        "'Related files' / 'Current scene scripts' / 'Open in editor' = also the user's project. "
        "When editing or fixing a file, use the path shown (e.g. Assets/enemy.gd); call read_file(path) if you need full content."
    )
    if engine_version:
        environment_parts.append(f"engine: {engine_version}")
    if active_scene_path:
        environment_parts.append(f"Current scene (open in editor): {active_scene_path}. Use for create_node (or omit scene_path).")
    # File-preview context: when the user is clearly asking to create a new file/script, tell the model it does not exist.
    q_lower = question.strip().lower()
    if any(
        phrase in q_lower
        for phrase in ("create ", "add a script", "new script", "new file", "create a ", "make a script", "make a file")
    ):
        environment_parts.append(
            "The user may be asking to create a new script or file. That file does not exist yet. "
            "Use create_script or create_file then write_file; no need to call read_file before creating."
        )
    if scene_dimension == "2d":
        environment_parts.append("SCENE TYPE: 2D")
        environment_parts.append(
            "ALLOWED NODE TYPES: Node2D, CharacterBody2D, Sprite2D, CollisionShape2D, Camera2D, "
            "Label, Button, Control, TileMap, Area2D, StaticBody2D, etc. Do NOT use any Node3D/CharacterBody3D/3D types."
        )
    elif scene_dimension == "3d":
        environment_parts.append("SCENE TYPE: 3D")
        environment_parts.append(
            "ALLOWED NODE TYPES: Node3D, CharacterBody3D, MeshInstance3D, CollisionShape3D, Camera3D, "
            "Area3D, StaticBody3D, etc. Do NOT use any Node2D/CharacterBody2D/2D types."
        )
    if scene_root_class:
        environment_parts.append(f"Scene root class: {scene_root_class}.")
    if scene_tree:
        environment_parts.append("SCENE TREE:\n" + scene_tree)
    # Optional project.unity summary (main scene, autoloads).
    if project_root_abs:
        try:
            proj_text = read_project_file(project_root_abs, "Assets/project.unity", max_bytes=32_000)
            if proj_text:
                main_scene: Optional[str] = None
                autoloads: List[str] = []
                section = ""
                for raw in proj_text.splitlines():
                    line = raw.strip()
                    if not line or line.startswith(";") or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        section = line.strip("[]")
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key, value = key.strip(), value.strip().strip('"').strip("'")
                        if section == "application" and key == "run/main_scene":
                            main_scene = value
                        elif section.startswith("autoload") and key:
                            autoloads.append(key)
                if main_scene:
                    environment_parts.append(f"Project main scene: {main_scene}")
                if autoloads:
                    environment_parts.append("Project autoloads: " + ", ".join(autoloads[:15]))
        except Exception:
            pass
    # Unity API efficiency: short tips so the model generates idiomatic, efficient code.
    environment_parts.append(
        "Unity API efficiency: Use _physics_process(delta) for movement; _process(delta) for UI/non-physics. "
        "Cache node refs (e.g. onready var x = $Path or get once in _ready()). Use signals for decoupling. "
        "Prefer move_and_slide/move_and_collide for physics bodies; use call_deferred when modifying scene tree from callbacks. "
        "GDScript (.gd) files have exactly one 'extends ClassName' line at the top; when editing or writing a .gd file, never add a second extends—the file already has one. "
        "When fixing lint: Unity reports one error at a time; lint is re-run after each fix, so you may receive another message with the next error—fix the current one; more may follow."
    )
    environment_text = "\n".join(environment_parts) if environment_parts else None

    system_prompt = UNITY_AGENT_SYSTEM_PROMPT
    is_obscure = False
    optional_extras: List[str] = []
    if context_language:
        optional_extras.append(f"Preferred language: {context_language}")
    # Conversation history (plugin sends last N turns for multi-turn continuity).
    if request_context and request_context.get("extra"):
        conv_raw = (request_context.get("extra") or {}).get("conversation_history")
        if conv_raw is not None and isinstance(conv_raw, list) and len(conv_raw) > 0:
            conv_block = build_conversation_context(conv_raw)
            if conv_block:
                optional_extras.append("Recent conversation (for continuity):\n" + conv_block)
    # Open script tabs: first ~24 lines of top 5 scripts open in the Unity editor (aggressive context).
    if request_context and request_context.get("extra"):
        open_preview_raw = (request_context.get("extra") or {}).get("open_scripts_preview")
        if open_preview_raw and isinstance(open_preview_raw, list) and len(open_preview_raw) > 0:
            parts: List[str] = []
            for item in open_preview_raw[:5]:
                if isinstance(item, dict):
                    path_val = item.get("path") or item.get("path_str") or ""
                    prev = item.get("preview") or ""
                    if path_val or prev:
                        parts.append(f"--- Open in editor: {path_val} (first 24 lines) ---\n{prev}")
            if parts:
                optional_extras.append(
                    "Scripts currently open in the Unity Script Editor (user's project; Assets/ paths; first 24 lines each). "
                    "Call read_file(path) for full content before editing.\n\n"
                    + "\n\n".join(parts)
                )
    # User-dragged context: files/nodes dropped into the chat (FileSystem, Scene tree, Script list).
    extra = (request_context.get("extra") or {}) if request_context else {}
    pinned_context_note = extra.get("pinned_context_note")
    drag_intro = (
        str(pinned_context_note).strip()
        if pinned_context_note
        else "The user just dragged these items into context for this chat. Prioritize them when answering."
    )
    if request_context and request_context.get("extra"):
        pinned_files_raw = (request_context.get("extra") or {}).get("pinned_files")
        if pinned_files_raw and isinstance(pinned_files_raw, list) and len(pinned_files_raw) > 0:
            parts = []
            for item in pinned_files_raw[:12]:
                if isinstance(item, dict):
                    path_val = (item.get("path") or item.get("path_str") or "").strip()
                    content = (item.get("content") or "").strip()
                    if path_val or content:
                        parts.append(f"--- Pinned file (user-dragged): {path_val} ---\n{content or '(empty)'}")
            if parts:
                optional_extras.append(drag_intro + "\n\nPinned files:\n\n" + "\n\n".join(parts))
        pinned_nodes_raw = (request_context.get("extra") or {}).get("pinned_nodes")
        if pinned_nodes_raw and isinstance(pinned_nodes_raw, list) and len(pinned_nodes_raw) > 0:
            parts = []
            for item in pinned_nodes_raw[:20]:
                if isinstance(item, dict):
                    desc = (item.get("description") or "").strip()
                    scene_path = (item.get("scene_path") or "").strip()
                    node_path = (item.get("node_path") or "").strip()
                    if desc or node_path:
                        line = desc or f"Node path: {node_path}"
                        if scene_path and not (item.get("is_scene_root")):
                            line += f" (scene: {scene_path})"
                        parts.append(line)
            if parts:
                optional_extras.append(drag_intro + "\n\nPinned nodes/scene:\n\n" + "\n".join(parts))
        pinned_selections_raw = (request_context.get("extra") or {}).get("pinned_selections")
        if pinned_selections_raw and isinstance(pinned_selections_raw, list) and len(pinned_selections_raw) > 0:
            parts = []
            for item in pinned_selections_raw[:20]:
                if isinstance(item, dict):
                    text_val = (item.get("text") or "").strip()
                    source_path = (item.get("source_path") or "").strip()
                    if text_val:
                        header = "--- Pinned selection (user-dragged/highlighted)"
                        if source_path:
                            header += f" from {source_path}"
                        header += " ---"
                        parts.append(header + "\n" + text_val)
            if parts:
                optional_extras.append(drag_intro + "\n\nPinned selections:\n\n" + "\n\n".join(parts))
    if is_obscure:
        optional_extras.append(
            "Heuristic: This seems like an obscure area; consider lower-importance snippets too."
        )
    # When fixing lint or user asks to fix a file: inject GDScript 4 rules so the model fixes common parse errors correctly.
    if (errors_text and str(errors_text).strip()) or ("fix" in question.lower() or "lint" in question.lower()):
        gd4_rules = (
            "GDScript 4.x lint fix rules (apply when fixing .gd files):\n"
            "- 'Expected type specifier after \"is\"': Use == null or != null for null checks, not 'is null'. "
            "The 'is' keyword is only for type checks (e.g. if x is Node2D). Replace 'if x is null' with 'if x == null' and 'if x is not null' with 'if x != null'.\n"
            "- 'Member \"velocity\" redefined': CharacterBody2D/CharacterBody3D already have a built-in 'velocity' property. Remove the duplicate 'var velocity: Vector2 = ...' or 'var velocity: Vector3 = ...' declaration; use the built-in property.\n"
            "- 'Too many arguments for move_and_slide()': In Unity 4, move_and_slide() takes no arguments. Set the node's 'velocity' property, then call move_and_slide() with no args. Do not assign the return value to velocity (it returns a bool).\n"
            "- 'Assignment is not allowed inside an expression': You cannot assign and use in the same expression; split into two statements or fix the invalid syntax."
        )
        optional_extras.append(gd4_rules)
    # Client-owned lint repair memory:
    # The plugin computes `context.extra.lint_repair_memory` locally and injects it here.
    # This keeps the hosted backend stateless (no server-side repair-memory queries).
    try:
        extra = (request_context.get("extra") or {}) if request_context else {}
        if isinstance(extra, dict):
            lint_repair_memory = extra.get("lint_repair_memory")
            if isinstance(lint_repair_memory, str) and lint_repair_memory.strip():
                optional_extras.append(lint_repair_memory.strip())
    except Exception:
        pass

    # Component/class context: when the user has a node type selected, inject its docs so the LLM knows properties for modify_attribute.
    # Include base class docs for custom/obscure types (e.g. class_name Player extends CharacterBody2D -> also fetch CharacterBody2D docs).
    selected_node_base_type: Optional[str] = None

    # OpenViking: retrieve session memories for this chat (when chat_id present).
    retrieved_memories: List[str] = []
    if chat_id:
        try:
            mems = openviking_find_memories(chat_id, question, top_k=5)
            for m in mems:
                text = (m.get("overview") or m.get("content") or m.get("abstract") or "").strip()
                if text:
                    retrieved_memories.append(text)
        except Exception:
            pass

    blocks = build_ordered_blocks(
        model=model,
        system_instructions=system_prompt,
        question=question,
        active_file_path=active_file_path,
        active_file_text=active_file_text,
        errors_text=errors_text,
        related_files=related_files,
        recent_edits=recent_edits_text,
        optional_extras=optional_extras,
        include_system_in_user=False,
        environment_text=environment_text,
        current_scene_scripts=current_scene_scripts if current_scene_scripts else None,
        exclude_block_keys=exclude_block_keys,
        retrieved_memories=retrieved_memories if retrieved_memories else None,
    )
    limit = get_context_limit(model)
    # When context fills over 50%, drop lowest-priority blocks first (extras).
    user_content, _dbg = blocks_to_user_content(
        blocks, limit=limit, reserve=4096, fill_target_ratio=0.5
    )
    context_view_for_response = build_context_view(blocks, _dbg)
    # Verbose decision log for the context viewer UI.
    context_decision_log: List[str] = []
    context_decision_log.append(
        f"Context limit: {limit} tokens; reserve: 4096; target cap: 50% fill"
    )
    context_decision_log.extend(_dbg.get("log", []))
    user_content += (
        "\n\n[AGENT MODE: When the user asks to fix or edit a file, you MUST call read_file(path) then apply_patch(path, old_string, new_string) or write_file(path, content). Do not only describe the fix.]\n"
        "You may call read_file(path) to read any project file; list_files(path, recursive, extensions) to find all files of a type (e.g. all .svg); "
        "read_import_options(path) to see import settings; modify_attribute(target_type='import', path=..., attribute=..., value=...) to change them (e.g. attribute=compress, value=true for lossless SVG). "
        "Use modify_attribute(target_type='node', scene_path=..., node_path=..., attribute=..., value=...) for node properties. "
        "For create_node: use the current scene (omit scene_path or pass 'current') and parent_path /root. Use 2D node types (Node2D, CharacterBody2D, Sprite2D) in 2D scenes and 3D types (Node3D, CharacterBody3D) in 3D scenes.\n"
        "To add a script to a node: create_script(path, extends_class, initial_content), then modify_attribute(target_type='node', scene_path=..., node_path=..., attribute='script', value='Assets/path/to/script.gd').\n"
        "For fixes/edits: read_file(path) first, then apply_patch(path, old_string, new_string) or write_file(path, content). You will receive written content in the tool result; do not call read_file to verify. "
        "In GDScript (.gd) files the first line is already 'extends ClassName'; when using write_file or apply_patch on a .gd file, do not add or duplicate an extends line—only one extends per script. "
        "If the existing context is enough, answer directly.\n"
    )

    # Pydantic AI agent: single run with tools; tool execution via execute_tool (tool_runner).
    read_file_cache: Dict[str, str] = {}
    deps = UnityQueryDeps(
        project_root_abs=project_root_abs,
        active_scene_path=active_scene_path,
        active_file_path=active_file_path,
        extra=(request_context.get("extra") or {}) if request_context else {},
        read_file_cache=read_file_cache,
    )
    agent = create_unity_agent(model=model_override or model)
    result = agent.run_sync(user_content, deps=deps)
    tool_call_results = _extract_tool_calls_from_agent_result(result)
    answer = (result.output or "").strip()
    usage_obj = build_context_usage(
        model,
        [user_content],
    )
    run_usage = getattr(result, "usage", None)
    if run_usage is not None:
        total_prompt_tokens = getattr(run_usage, "input_tokens", None) or getattr(run_usage, "prompt_tokens", 0) or 0
        total_completion_tokens = getattr(run_usage, "output_tokens", None) or getattr(run_usage, "completion_tokens", 0) or 0
        if total_prompt_tokens or total_completion_tokens:
            _log_usage_and_cost(
                model=model,
                prompt_tokens=int(total_prompt_tokens),
                completion_tokens=int(total_completion_tokens),
                context="query_with_tools",
            )
            # Usage persistence is handled client-side in the Unity plugin.
    # OpenViking: commit this turn for memory extraction (fire-and-forget).
    if chat_id and answer:
        try:
            openviking_add_turn_and_commit(
                chat_id,
                [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
            )
        except Exception:
            pass
    return answer, [], tool_call_results, {
        "model": usage_obj.model,
        "limit_tokens": usage_obj.limit_tokens,
        "estimated_prompt_tokens": usage_obj.estimated_prompt_tokens,
        "percent": usage_obj.percent,
        "context_view": context_view_for_response,
        "context_decision_log": context_decision_log,
    }


def _run_composer_query(
    question: str,
    context_language: Optional[str],
    request_context: Optional["QueryContext"],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_override: Optional[str] = None,
    composer_mode: Optional[Literal["agent", "ask"]] = None,
) -> Tuple[str, List[SourceChunk], List[ToolCallResult], Dict[str, Any]]:
    """
    Unity Composer v2: single-turn call to a fine-tuned model that outputs tool calls directly
    (no RAG, no tool loop). Parses <tool_call>...</tool_call> blocks.
    """
    client, model = _openai_client_and_model(
        api_key=api_key, base_url=base_url, model=model_override
    )
    if client is None:
        return (
            "No Composer model configured. Set API key and model (e.g. unity-composer) in settings.",
            [],
            [],
            {"model": "", "limit_tokens": 0, "estimated_prompt_tokens": 0, "percent": 0.0},
        )

    extra = (request_context.get("extra") or {}) if request_context else {}
    mode = composer_mode or "agent"
    system_prompt = (
        COMPOSER_V2_SYSTEM_PROMPT_ASK if mode == "ask" else (COMPOSER_V2_SYSTEM_PROMPT_AGENT or COMPOSER_SYSTEM_PROMPT)
    )
    user_parts: List[str] = [question]
    if extra.get("active_file_text"):
        user_parts.append("Current file content:\n" + str(extra["active_file_text"]))
    if extra.get("scene_tree"):
        user_parts.append("Scene tree:\n" + str(extra["scene_tree"]))
    recent = extra.get("recent_edits")
    if recent and isinstance(recent, list) and len(recent) > 0:
        parts: List[str] = []
        for e in recent[:8]:
            if isinstance(e, str) and e.strip():
                parts.append(e.strip())
            elif e is not None:
                s = str(e).strip()
                if s:
                    parts.append(s)
        if parts:
            user_parts.append("Recent edits:\n" + "\n\n".join(parts))
    if extra.get("lint_output"):
        user_parts.append("Lint output:\n" + str(extra["lint_output"]))
    if extra.get("active_scene_path"):
        user_parts.append("Current scene: " + str(extra["active_scene_path"]))
    if extra.get("scene_dimension"):
        user_parts.append("Scene type: " + str(extra["scene_dimension"]))
    if request_context and request_context.get("current_script"):
        user_parts.append("Active script: " + str(request_context.get("current_script")))
    conv = extra.get("conversation_history")
    if conv and isinstance(conv, list) and len(conv) > 0:
        conv_lines = []
        for m in conv[-6:]:
            if isinstance(m, dict):
                r = m.get("role", "")
                c = m.get("content", "")
                if r and c is not None:
                    conv_lines.append(f"{r}: {str(c)[:500]}")
        if conv_lines:
            user_parts.append("Recent conversation:\n" + "\n".join(conv_lines))
    user_content = "\n\n".join(user_parts)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    _log_llm_input(model=model, context="composer", input_payload=messages)
    try:
        completion = client.chat.completions.create(model=model, messages=messages)
    except Exception as e:
        return (
            "Composer request failed: " + str(e),
            [],
            [],
            {"model": model, "limit_tokens": 0, "estimated_prompt_tokens": 0, "percent": 0.0},
        )
    content = (completion.choices[0].message.content or "").strip()
    usage = getattr(completion, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0
    completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0
    if usage:
        # Usage persistence is handled client-side in the Unity plugin.
        pass
    answer, raw_tool_calls = _parse_composer_response(content)
    tool_results: List[ToolCallResult] = [
        {
            "tool_name": tc["name"],
            "arguments": tc.get("arguments") or {},
            "output": None,
        }
        for tc in raw_tool_calls
    ]
    limit = get_context_limit(model)
    context_usage = {
        "model": model,
        "limit_tokens": limit,
        "estimated_prompt_tokens": int(prompt_tokens),
        "percent": (int(prompt_tokens) + int(completion_tokens)) / limit if limit else 0.0,
    }
    return answer, [], tool_results, context_usage


@app.get("/health")
async def health() -> Dict[str, str]:
    """
    Simple health check so the Unity plugin can verify connectivity.
    """
    return {"status": "ok"}


@app.get("/test/backends")
async def test_backends() -> Dict[str, Any]:
    """
    Return backend identifiers, endpoints, and default models for testing and UI.
    Use this to switch between RAG (GPT-4.1-mini) and Unity Composer easily.
    """
    default_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    composer_model = os.getenv("COMPOSER_MODEL") or default_model
    return {
        "rag": {
            "endpoint": "/query",
            "stream_endpoint": "/query_stream_with_tools",
            "default_model": default_model,
            "description": "RAG + tool loop (e.g. gpt-4.1-mini)",
        },
        "composer": {
            "endpoint": "/composer/query",
            "stream_endpoint": "/composer/query_stream_with_tools",
            "default_model": composer_model,
            "description": "Unity Composer fine-tuned model, tool_calls in response",
        },
    }


class IndexStatusResponse(TypedDict, total=False):
    chroma_docs: int
    chroma_project_code: int
    repo_index_error: Optional[str]
    repo_index_files: Optional[int]
    repo_index_edges: Optional[int]


@app.get("/index_status")
async def index_status(project_root: Optional[str] = None) -> Dict[str, Any]:
    """
    Return indexing facts for optional repo index stats.

    NOTE: Legacy support: older versions also reported Chroma collection counts for
    docs/project_code via rag_core.get_collections(). That retrieval path is now
    deprecated and removed; chroma_* fields are always 0.
    """
    out: Dict[str, Any] = {
        "chroma_docs": 0,
        "chroma_project_code": 0,
    }
    # Deprecated: the Unity plugin now owns repo proximity indexing.
    # We keep this endpoint for backward compatibility with older UI versions.
    if project_root and project_root.strip():
        out["repo_index_error"] = "Deprecated: repo index stats are client-side in the Unity plugin."
    return out


#
# Deprecated endpoints removed:
# - POST /lint_memory/record_fix
# - GET /lint_memory/search
# - POST /edit_events/create
# - GET /edit_events/list
# - GET /edit_events/{edit_id}
# - POST /edit_events/undo/{edit_id}
# - GET /usage
#


class LintRequest(TypedDict, total=False):
    """Request to run Unity script linter on a file. Run from backend to avoid spawning Unity from inside the editor (which can crash)."""
    project_root_abs: str
    path: str  # Assets/path or path relative to project


def _get_unity_bin() -> str:
    """Unity executable for headless lint. Prefer UNITY_BIN env; else 'unity' (or unity.exe on Windows)."""
    bin_path = os.getenv("UNITY_BIN", "").strip()
    if bin_path:
        return bin_path
    if sys.platform == "win32":
        return "unity.exe"
    return "unity"


# Timeout for headless Unity lint (prevents hang/crash from infinite loops or slow load).
_LINT_SUBPROCESS_TIMEOUT_SECONDS = 60.0

# Cache for /lint: (project_root, path, mtime) -> (result, timestamp). TTL in seconds.
_LINT_CACHE_TTL_SECONDS = 10.0
_lint_cache: dict[tuple[str, str, float], tuple[Dict[str, Any], float]] = {}


def _lint_cache_key(project_root: str, path: str) -> tuple[str, str, float]:
    """Key is (project_root, path, mtime). mtime=0 if file missing."""
    abs_path = os.path.join(project_root, path)
    mtime = 0.0
    if os.path.isfile(abs_path):
        mtime = os.path.getmtime(abs_path)
    return (project_root, path, mtime)


@app.post("/lint")
async def run_lint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run Unity headless linter (--script path --check-only) on a script file.
    Uses the same parser as the editor so errors match what the user sees.
    Called by the plugin so the editor never spawns a second Unity process (which can crash).
    Do not use --debug: it can cause infinite loops when the script has parser errors.
    Results are cached by (project_root, path, mtime) for 10s to avoid redundant Unity spawns.
    """
    project_root = str(payload.get("project_root_abs") or "").strip().rstrip("/\\")
    path = str(payload.get("path") or "").strip().replace("\\", "/")
    if path.startswith("Assets/"):
        path = path[6:].lstrip("/")
    if not project_root or not path:
        return {"success": False, "output": "project_root_abs and path are required", "exit_code": -1}
    if not os.path.isdir(project_root):
        return {"success": False, "output": f"Project root not found: {project_root}", "exit_code": -1}
    now = time.monotonic()
    cache_key = _lint_cache_key(project_root, path)
    if cache_key in _lint_cache:
        cached_result, cached_at = _lint_cache[cache_key]
        if (now - cached_at) < _LINT_CACHE_TTL_SECONDS:
            return cached_result
        del _lint_cache[cache_key]
    unity_bin = _get_unity_bin()
    # Unity docs: --check-only must be used with --script. Path is relative to project (Assets/).
    # --editor loads the project for full type checking; --headless avoids GUI. No --debug (can hang on errors).
    args = [unity_bin, "--headless", "--editor", "--path", project_root, "--script", path, "--check-only"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=_LINT_SUBPROCESS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "success": False,
                "output": f"Lint timed out after {int(_LINT_SUBPROCESS_TIMEOUT_SECONDS)}s. Unity may have hung (try simplifying the script or set UNITY_BIN to a stable build).",
                "exit_code": -1,
            }
        out = (stdout_bytes or b"").decode("utf-8", errors="replace") + (stderr_bytes or b"").decode("utf-8", errors="replace")
        out = out.strip()
        result = {"success": proc.returncode == 0, "output": out, "exit_code": proc.returncode or 0}
        _lint_cache[cache_key] = (result, now)
        return result
    except FileNotFoundError:
        return {
            "success": False,
            "output": f"Unity not found: {unity_bin}. Set UNITY_BIN to the full path to the Unity editor executable.",
            "exit_code": -1,
        }
    except Exception as e:
        return {"success": False, "output": str(e), "exit_code": -1}


@app.post("/query")
async def query_rag(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    RAG endpoint that:
    - Uses retrieved documentation + example project code snippets (vector retrieval is removed in this repo).
    - Builds a context window and answers the question.
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /query", client_host, question, _cyan)

    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None
    top_k = int(payload.get("top_k") or 8)
    max_tool_rounds = payload.get("max_tool_rounds")
    max_tool_rounds_int = int(max_tool_rounds) if max_tool_rounds is not None else 5
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    model_override = payload.get("model")

    def run():
        return _run_query_with_tools(
            question=question,
            context_language=context_language,
            request_context=context,
            top_k=top_k,
            max_tool_rounds=max_tool_rounds_int,
            api_key=api_key,
            base_url=base_url,
            model_override=model_override,
        )

    answer, snippets, tool_calls, context_usage = await asyncio.to_thread(run)

    return {
        "answer": answer,
        "snippets": snippets,
        "tool_calls": tool_calls,
        "context_usage": context_usage,
    }


# Sentinel line the plugin uses to parse tool_calls from the stream.
_STREAM_TOOL_CALLS_PREFIX = "\n__TOOL_CALLS__\n"


@app.post("/query_stream_with_tools")
async def query_stream_with_tools(payload: Dict[str, Any], request: Request):
    """
    Same as /query (RAG + tools) but streams the answer in chunks, then appends
    a line __TOOL_CALLS__\\n + JSON array of tool_calls. Use when editor actions
    are enabled so the user sees progressive output and still gets tool execution.
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /query_stream_with_tools", client_host, question, _green)

    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None
    top_k = int(payload.get("top_k") or 8)
    max_tool_rounds = payload.get("max_tool_rounds")
    max_tool_rounds_int = int(max_tool_rounds) if max_tool_rounds is not None else 5
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    model_override = payload.get("model")

    def run():
        return _run_query_with_tools(
            question=question,
            context_language=context_language,
            request_context=context,
            top_k=top_k,
            max_tool_rounds=max_tool_rounds_int,
            api_key=api_key,
            base_url=base_url,
            model_override=model_override,
        )

    answer, snippets, tool_calls, context_usage = await asyncio.to_thread(run)

    def stream_iter():
        # Stream answer in small chunks so UI updates progressively.
        chunk_size = 80
        for i in range(0, len(answer), chunk_size):
            yield answer[i : i + chunk_size]
        # Then send tool_calls so the client can run editor actions.
        payload_list = tool_calls
        yield _STREAM_TOOL_CALLS_PREFIX + json.dumps(payload_list) + "\n"
        yield "\n__USAGE__\n" + json.dumps(context_usage) + "\n"

    return StreamingResponse(
        stream_iter(), media_type="text/plain; charset=utf-8"
    )


# --- Unity Composer (fine-tuned model, tool_calls directly) ---


@app.post("/composer/query")
async def composer_query(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    Unity Composer: single-turn call to a fine-tuned model that outputs tool_calls
    directly. Same request/response shape as /query so the plugin can switch by backend profile.
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /composer/query", client_host, question, _green)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    model_override = payload.get("model")
    composer_mode = payload.get("composer_mode")
    answer, snippets, tool_calls, context_usage = _run_composer_query(
        question=question,
        context_language=context_language,
        request_context=context,
        api_key=api_key,
        base_url=base_url,
        model_override=model_override,
        composer_mode=composer_mode,
    )
    return {
        "answer": answer,
        "snippets": snippets,
        "tool_calls": tool_calls,
        "context_usage": context_usage,
    }


@app.post("/composer/query_stream_with_tools")
async def composer_query_stream_with_tools(payload: Dict[str, Any], request: Request):
    """
    Same as /composer/query but streams answer text then __TOOL_CALLS__ + JSON.
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /composer/query_stream_with_tools", client_host, question, _green)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    model_override = payload.get("model")
    composer_mode = payload.get("composer_mode")

    def run():
        return _run_composer_query(
            question=question,
            context_language=context_language,
            request_context=context,
            api_key=api_key,
            base_url=base_url,
            model_override=model_override,
            composer_mode=composer_mode,
        )

    answer, snippets, tool_calls, context_usage = await asyncio.to_thread(run)

    def stream_iter():
        chunk_size = 80
        for i in range(0, len(answer), chunk_size):
            yield answer[i : i + chunk_size]
        payload_list = tool_calls
        yield _STREAM_TOOL_CALLS_PREFIX + json.dumps(payload_list) + "\n"
        yield "\n__USAGE__\n" + json.dumps(context_usage) + "\n"

    return StreamingResponse(
        stream_iter(), media_type="text/plain; charset=utf-8"
    )


@app.post("/composer/query_stream")
async def composer_query_stream(payload: Dict[str, Any], request: Request):
    """
    Composer streaming (answer text only, no tool_calls suffix).
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /composer/query_stream", client_host, question, _dim)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    model_override = payload.get("model")
    composer_mode = payload.get("composer_mode")

    def run():
        return _run_composer_query(
            question=question,
            context_language=context_language,
            request_context=context,
            api_key=api_key,
            base_url=base_url,
            model_override=model_override,
            composer_mode=composer_mode,
        )

    answer, _, _, _ = await asyncio.to_thread(run)

    def stream_iter():
        chunk_size = 80
        for i in range(0, len(answer), chunk_size):
            yield answer[i : i + chunk_size]

    return StreamingResponse(
        stream_iter(), media_type="text/plain; charset=utf-8"
    )


@app.post("/query_stream")
async def query_stream(payload: Dict[str, Any], request: Request):
    """
    Streaming variant of /query.

    - Reuses the same RAG retrieval to build initial context.
    - If an OpenAI client is available, streams the answer text incrementally
      using chat completions in streaming mode.
    - If no OpenAI client is configured, streams a single fallback answer.
    """
    client_host = request.client.host if request.client else "unknown"
    question = str(payload.get("question") or "").strip()
    _log_rag_request("POST /query_stream", client_host, question, _dim)
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    context_language = context.get("language") if context else None

    client, model = _openai_client_and_model(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        model=payload.get("model"),
    )

    system_prompt = (
        "You are a Unity 4.x development assistant. "
        "Answer the user's question. When writing code examples, use the user's preferred language if given. "
        "Show your reasoning: first output your thinking inside <think>...</think> tags (what you are considering, what you will do), then your final answer after the closing tag."
    )

    user_prompt_lines: List[str] = []
    user_prompt_lines.append(f"Question: {question}\n")
    if context_language:
        user_prompt_lines.append(f"Preferred language: {context_language}\n")
    user_prompt_lines.append(
        "\nStream your response: first <think>...</think> with your reasoning, then your final answer (with any code examples) after the closing tag.\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_prompt_lines)},
    ]

    _log_llm_input(model=model, context="query_stream", input_payload=messages)

    if client is None:
        def fallback_iter():
            yield "OpenAI client not configured. Set OPENAI_API_KEY to use the assistant."

        return StreamingResponse(fallback_iter(), media_type="text/plain; charset=utf-8")

    def stream_iter():
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        prompt_tokens = 0
        completion_tokens = 0
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except Exception:
                delta = ""
            if delta:
                yield delta
            # Capture usage information from the final chunk if present.
            try:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or prompt_tokens
                    completion_tokens = (
                        getattr(usage, "completion_tokens", 0) or completion_tokens
                    )
            except Exception:
                pass

        if prompt_tokens or completion_tokens:
            _log_usage_and_cost(
                model=model,
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                context="query_stream",
            )
            # Usage persistence is handled client-side in the Unity plugin.

    return StreamingResponse(stream_iter(), media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn

    try:
        port = int(os.getenv("PORT", "8001"))
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            reload=True,
            log_level="warning",
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        sys.exit(0)

