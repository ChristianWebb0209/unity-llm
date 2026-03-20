## Project Context for Future LLMs

This file is the **single source of truth** for how the **Godot LLM Assistant** works: architecture, tools, conventions, and important paths. **Read this before making changes.**

---

## Quick reference for future agents

- **Change a tool (add/rename/parameters)**  
  Tool loop and execution are implemented via **Pydantic AI**. Edit `rag_service/app/tools/definitions.py` for tool definitions and handlers; ensure the agent wiring is correct in `rag_service/app/tools/agent.py` and that server-side execution (when `project_root_abs` is set) is implemented in `rag_service/app/tools/runner.py`. Plugin: `godot_plugin/addons/godot_ai_assistant/tools/editor_tool_executor.gd` dispatches execute_on_client actions to `tools/file.gd`, `tools/fs.gd`, etc.

- **Change what the LLM sees (context / tools)**  
  `rag_service/app/main.py`: `_run_query_with_tools` builds RAG + context blocks and user content, then calls the Pydantic AI agent (`godot_agent.run_sync`). Agent instructions and tool registration live in `rag_service/app/tools/agent.py` and `rag_service/app/tools/definitions.py`; tool execution is in `dispatch_tool_call` / `rag_service/app/tools/runner.py`. Context block order and budgets: `rag_service/app/services/context/` (context_builder, budget). Plugin sends `context.extra.conversation_history` and `context.extra.chat_id` (for OpenViking session memory; see §3.4).

- **Change plugin UI (tabs, chat, diff, history)**  
  `godot_plugin/addons/godot_ai_assistant/ai_dock.gd` (logic) and `ai_dock.tscn` (scene). Tab selection uses **child node name** (e.g. `History`, `Settings`), not tab index.

- **Edit History data**  
  Plugin-only: Edit History tab uses `GodotAIEditStore` and persists to `user://godot_ai_assistant/edit_history/edits.json`.
  Backend edit-history endpoints are deprecated (see `/edit_events/*` routes).

- **Plugin not loading**  
  If the dock does not appear: check Godot Output for parse/script errors. Common causes: wrong node path in @onready (use `get_node_or_null()` in `_ready()` for optional nodes), or GDScript/Godot 4 API misuse (see §11). Open the project from the folder that contains `project.godot` (e.g. `godot_plugin`), not the parent repo root.

- **read_file / list_directory / search_files (server when project open)**  
  When the plugin sends `context.extra.project_root_abs`, `tool_runner.execute_tool` runs `read_file`, `list_directory`, and `search_files` on the server (Pydantic AI agent tool loop) and returns real results to the LLM. **read_file** is cached per request in `GodotQueryDeps.read_file_cache`. Lint is server-based (`/lint`); after edits the plugin can auto-send one follow-up request with lint output so the model can fix in the same “turn” (see §6.1).

- **Run backend**  
  From `rag_service/`: `.\run_backend.ps1` (or `uvicorn app.main:app --reload`). Default URL `http://127.0.0.1:8000`; plugin uses Settings or `rag_service_url`.

- **Quick test**  
  Backend: `GET http://127.0.0.1:8000/health` → `{ "status": "ok" }`. Plugin: enable Tools, ask something that triggers read_file or a file edit; check Pending & Timeline and Edit History. Lint: ask to create/edit a script and confirm lint runs (server `/lint`) and output appears in chat.

---

## 1. High-Level Vision

- **Goal**: An AI-native assistant *inside Godot* that:
  - Lives as a **Godot editor plugin**.
  - Talks to a **RAG + LLM backend**.
  - Understands **Godot 4.x** docs, patterns, and **real project code** in:
    - GDScript (`.gd`)
    - C# (`.cs`)
    - Godot shaders (`.gdshader`)
- Current focus:
  - **RAG** (docs + project_code), **context builder** (budgeted blocks, conversation history, repo-index “related files”), **tools**: server-side exploration when `project_root_abs` is set (read_file, list_files, list_directory, search_files, read_import_options), **index-backed tools** (project_structure, find_scripts_by_extends, find_references_to), **editor tools** (file/scene/node edits in Godot), **lint-after-edit** (server lint + auto-fix; one auto follow-up request with lint output so the model can fix without user typing again).
  - Plugin: streaming answers, apply-immediately edits, timeline + Revert, 🟢🟡🔴 indicators, multi-turn context (last N messages sent as `conversation_history`).

---

## 2. Repo Layout (Key Paths)

- `rag_service/` – Python 3.11 backend + tooling and data.
- `godot_plugin/addons/godot_ai_assistant/` – Godot editor plugin.
- `godot_knowledge_base/` – Scraped docs + curated code.
- (Removed) ChromaDB vector store used to live at `rag_service/data/chroma_db/` for runtime retrieval.

### 2.1 Plugin folder layout (`godot_plugin/addons/godot_ai_assistant/`)

- **Root**: `plugin.cfg`, `godot_ai_assistant.gd`, `ai_dock.tscn`, `ai_dock.gd` (orchestrator), `settings.gd`, `ai_edit_store.gd`.
- **core/** – Shared backend/context: `backend_client.gd` (HTTP, stream, query_json), `context_payload.gd` (build context dict: engine, script, scene, project_root, lint_output, conversation_history).
- **chat/** – Chat UI and rendering: `chat_state.gd`, `chat_renderer.gd`, `activity_state.gd`, `markdown_renderer.gd`.
- **backend/** – Backend API and tool orchestration: `backend_api.gd` (query for tools, log edit event, post lint fix), `tool_runner.gd` (run tool_calls, format summaries/chat section).
- **ui_tabs/** – Per-tab logic: `changes_tab.gd`, `history_tab.gd`, `settings_tab.gd`; optional popup `settings_panel.gd` + `settings_panel.tscn` (main Settings UI is the dock’s Settings tab, see `settings_tab.gd`).
- **editor/** – Code that changes the Godot editor itself: `editor_decorator.gd` (file/node indicators in script tabs, FileSystem tree, Scene tree).
- **tools/** – Editor tool executor and actions: `editor_tool_executor.gd` (public API: execute, execute_async, preview_file_change; path helpers); `file.gd` (GodotAIFile), `fs.gd` (GodotAIFS), `import.gd` (GodotAIImport), `node.gd` (GodotAINode), `previews.gd` (GodotAIPreviews); **tools/lint/** – `server_lint.gd` (GodotAIServerLint), `lint_capture_logger.gd` (Logger capture), `lint_autofix.gd` (GodotAILintAutofix), `test_lint_capture.gd` (tests). **Run lint capture tests**: in Godot with the plugin enabled, use **Project → Run lint capture tests**; see Output for pass/fail.

Important subpaths:

- Backend:
  - `rag_service/app/main.py` – FastAPI + RAG.
  - `rag_service/run_backend.ps1` – start backend.
  - `rag_service/run_tools.ps1` – unified launcher for tools.
- Docs pipeline:
  - `rag_service/tools/docs-parser/scrape_godot_docs.py` – crawler → markdown.
- `rag_service/tools/docs-parser/index_docs.py` – optional indexing step (not used for runtime retrieval in this repo).
  - `godot_knowledge_base/docs/4.6/**` – scraped docs.
- Project pipeline:
  - (Removed) `rag_service/scripts/analyze_project.py` – project code analysis/indexing removed from this repo.
  - `godot_knowledge_base/code/demos/<slug>/` – selected important scripts/shaders.
- Repo proximity (structural graph):
  - `godot_plugin/addons/godot_ai_assistant/core/stores/repo_index_store.gd` – client-side JSON cache (`user://godot_ai_assistant/repo_index/<repo_id>.json`) for one-hop related `res://` paths.
- Repair memory (lint fixes):
  - `godot_plugin/addons/godot_ai_assistant/core/stores/lint_memory_store.gd` – client-side JSON store (`user://godot_ai_assistant/lint_memory/lint_memory.json`) used to inject `context.extra.lint_repair_memory`.

---

## 3. Backend (`rag_service/app/main.py`)

### 3.1 FastAPI Endpoints

- `GET /health`:
  - Returns `{ "status": "ok" }`.
  - Used by Godot plugin + test scripts to confirm backend is up.

- `POST /query`:
  - Request model:
    - `question: str`.
    - `context` (optional): `engine_version`, `language`, `selected_node_type`, `current_script`, `extra` (includes `project_root_abs`, `active_file_text`, `active_scene_path`, `scene_tree`, `lint_output`, `conversation_history`, `exclude_block_keys`, `chat_id`).
    - `top_k: int = 8`.
    - `max_tool_rounds: Optional[int] = None` (default 5 when omitted; max tool-call rounds per request).
  - Response model:
    - `answer: str` – markdown answer text.
    - `snippets: List[SourceChunk]`:
      - `id`, `source_path`, `score`, `text_preview`, `metadata`.
    - `tool_calls: List[ToolCallResult]` (optional):
      - `tool_name: str`
      - `arguments: Dict[str, Any]`
      - `output: Any`
    - `context_usage: Dict` (optional):
      - `model: str`
      - `limit_tokens: int` (model context limit)
      - `estimated_prompt_tokens: int` (cheap local estimate)
      - `percent: float`

- `POST /query_stream`:
  - Same request body as `/query`.
  - Streams back the answer text as plain UTF‑8 chunks so the Godot dock can
    display it incrementally while the model is still generating.

- `POST /query_stream_with_tools`:
  - Streams answer text like `/query_stream`, then appends two sentinel blocks:
    - `__TOOL_CALLS__` followed by JSON array of tool calls (so the editor can run them).
    - `__USAGE__` followed by JSON `context_usage` (so the dock can update its UI).

### 3.2 Environment & OpenAI

- `.env` in `rag_service` is loaded via `python-dotenv`:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL` (default `"gpt-4.1-mini"`)
  - `OPENAI_EMBED_MODEL` (default `"text-embedding-3-small"`)
  - `OPENAI_BASE_URL` (optional)
- `get_openai_client()`:
  - Returns an `OpenAI` client if `OPENAI_API_KEY` is set.
  - Returns `None` otherwise (backend falls back to a plain-text explanation).

### 3.3 ChromaDB Setup (removed)

ChromaDB vector indexing and Chroma-backed retrieval were removed from this repo.
Runtime retrieval relies on:
- active file context + plugin-provided one-hop related files,
- optional current scene scripts,
- optional OpenViking session memory.

### 3.4 Retrieval Strategy (legacy; disabled)

The backend previously supported Chroma-based retrieval from:

- `docs` (official Godot docs)
- `project_code` (indexed scripts/shaders from other repos)

That retrieval is **disabled** in the current `/query` “tools loop” path; the model relies on:

- The user’s **active file**, **related files**, and **current scene scripts** (server-read from the project when `project_root_abs` is provided)
- Optional **conversation_history** and **OpenViking session memory**

### 3.6 Tools & Orchestration (`rag_service/app/tools/definitions.py`)

- Tools are `ToolDef` objects (name, description, parameters, handler). `get_openai_tools_payload()` builds the OpenAI `tools=[...]` payload. `_run_query_with_tools` runs RAG, then up to **max_tool_rounds** (default 5) of LLM + tool execution; tool results are fed back so the model can “explore then act” in one request.
- **Server-side when `project_root_abs` is set** (LLM sees real results in the same request):
  - **read_file**: `read_project_file()`; result cached per request for repeated reads.
  - **list_files**: File paths under res://, optional extension filter.
  - **list_directory**: Directory entries (name, path, is_dir) under a path.
  - **search_files**: Grep—files whose content contains the query; returns path + line matches.
  - **read_import_options**: Contents of the `.import` file for a resource.
- **Index-backed (server when `project_root_abs` set)**:
  - **project_structure**: `list_indexed_paths()` – list indexed file paths under a prefix (from repo index).
  - **find_scripts_by_extends**: Grep for `extends ClassName` in .gd/.cs.
  - **find_references_to**: `get_inbound_refs()` – files that reference a given res:// path (from repo index edges).
- **No RAG fetch in query path**: The assistant no longer retrieves from `docs` or `project_code` Chroma collections or exposes search_docs / search_project_code / request_component_context tools (simplified for fine-tuned model).
- **Editor-action tools** (return `execute_on_client: true`; plugin runs after stream): create_file, write_file, append_to_file, apply_patch, create_script, create_node, delete_file, modify_attribute, lint_file; **run_terminal_command**, **run_godot_headless**, **run_scene** (run.gd); **grep_search** (fs.gd); **get_node_tree** (scene_tree.gd); **get_signals**, **connect_signal** (signals.gd); **get_export_vars** (inspector.gd); **get_project_settings**, **get_autoloads**, **get_input_map** (project.gd); **check_errors** (editor_errors.gd).
- **Server-only (no project required)**: **fetch_url** (HTTP GET), **search_asset_library** (Godot Asset Library API). The backend edit-history tool `get_recent_changes` is deprecated and returns empty results.
- **Fast tool-call semantics** (minimize tokens): create_file(path) may have empty content (create then write_file); prefer apply_patch over write_file for edits; create_script supports optional `template` (e.g. character_2d, character_3d) for boilerplate; append_to_file for incremental writes. When `project_root_abs` is set, create_file, write_file, apply_patch, and append_to_file run on the server and return `content` in the tool result so the model does not need read_file to verify.
- **apply_patch**: accepts either (path, old_string, new_string) or (path, diff) with a unified-diff string.
- **modify_attribute**: `target_type='node'` (scene_path, node_path, attribute, value) or `target_type='import'` (path, attribute, value) for .import [params].
- **Godot API efficiency**: A short fixed block is injected into the environment (in `main.py` when building `environment_parts`) with tips: _physics_process for movement, cache node refs, signals, move_and_slide/move_and_collide, call_deferred when modifying scene tree from callbacks.
- **Lint**: Plugin POSTs to `/lint`; output shown in chat. After editor tool runs, if lint fails the plugin can **auto-send one follow-up** request with `lint_output` in context so the model can fix without the user typing again (§6.1).

---

## 4. ChromaDB Collections & Indexing (removed)

ChromaDB indexing into `docs` / `project_code` collections was removed from this repo.
At runtime, the backend relies on plugin-provided active/related context (and optional OpenViking memory).

### 4.1 Docs Collection (`docs`) (removed)

- Created and managed by `index_docs.py`.
- Always **rebuilt from scratch** on each `index_docs` run:
  - Existing `docs` collection is deleted.
  - New one is created with the current embedding function (OpenAI if available).
- Documents:
  - `id` = relative path under `docs_root` (e.g. `classes/class_node.md`).
  - `document` = full markdown file text.
  - `metadata`:
    - `path`: same as `id`.
    - `engine_version`: inferred from `docs_root` (e.g. `"4.6"`).

### 4.2 Project Code Collection (`project_code`)

- Created/updated by `analyze_project.py` (`index_in_chromadb`) (removed).
- On each ingest:
  - Loads `.env` to configure embeddings.
  - Reuses or creates `project_code` collection:
    - Prefer OpenAI embedding function if available.
    - If the collection already exists with a different embedding, logs a warning and uses the existing configuration (instead of crashing).
- Documents:
  - `id` = `"<project_slug>:<rel_path>"`.
  - `document` = full source code (script or shader).
  - `metadata`:
    - `project_id` – slug.
    - `path` – relative path (e.g. `src/Core/Nodes/Player/Player.gd`).
    - `language` – `"gdscript"`, `"csharp"`, `"gdshader"`.
    - `importance` – float.
    - `tags` – optional non-empty list if tags exist.

#### 4.2.1 How the LLM should treat `docs` vs `project_code`

- The **`docs` collection** is scraped from the **official Godot 4.x manuals**. It is the
  **authoritative source** for engine behavior, APIs, and built-in classes.
- The **`project_code` collection** contains **example scripts and shaders** from various projects.
  These are meant as **patterns and inspiration**, not as canonical definitions of how the engine works.
- When there is any tension between what the docs say and what project code seems to imply:
  - The LLM should **prefer `docs`**.
  - Project code is still valuable for idioms, patterns, and end-to-end examples, but must not
    override the official documentation.

### 4.3 Clean Reset Procedure (deprecated)

Vector indexing / ChromaDB collections were removed from this repo, so there is no vector reset procedure here.

---

## 5. Docs & project pipelines (reference)

- **Docs**: `scrape_godot_docs.py` (BFS crawl → markdown under `godot_knowledge_base/docs/4.6`). Vector indexing into Chroma is no longer used for runtime retrieval in this repo.
- **Project code**: project code analysis/indexing via `analyze_project.py` was removed (see note in §4).

---

## 6. Godot Plugin Dock & UX

- **Chat**:
  - **Enter** sends the message; **Shift+Enter** inserts a newline (gui_input on prompt TextEdit).
  - User message appears instantly; prompt is cleared and the request runs. Response streams in with a typing cursor; the message does not disappear while streaming.
  - **You** vs **Assistant** are visually distinct (blue-tinted right-aligned block for user, green-tinted block for assistant with labels).
  - Default font size is **18** (configurable in Settings). Copy uses `get_parsed_text()` and `DisplayServer.clipboard_set(...)`.
  - **Tools** checkbox: when checked, the dock uses `POST /query_stream_with_tools`; tool calls with `execute_on_client: true` are run by `GodotAIEditorToolExecutor`. Context usage label near chat tabs (e.g. `Ctx: 2% (792/32768)`).
- **Tabs**:
  - **Main tabs** (Chat, Edit History, Settings, Pending & Timeline): `TabContainer.get_tab_bar().drag_to_rearrange_enabled = true`. Tab-change logic uses the **selected child’s node name** (e.g. `Settings`, `History`), not fixed indices, so it still works after the user reorders tabs.
  - **Chat tabs** (Chat 1, Chat 2, …): `TabBar.drag_to_rearrange_enabled = true`; `active_tab_rearranged` is connected so `_chats` is reordered to match the new tab order.
- **Pending & Timeline**: Diff preview (OldText/NewText) shows when a file item is selected; safe node resolution and minimum size so the panel stays visible.
- **Edit History**: Flat ItemList + detail panel (timestamp, summary, files changed, prompt, lint).
  Data comes from local `GodotAIEditStore` persisted to `user://godot_ai_assistant/edit_history/edits.json` (undo/revert is local).
- **Plugin load**: If the dock scene fails to load, a fallback panel with an error message is shown; check Output for errors.
- The plugin passes `EditorInterface` into the dock via `set_editor_interface()` so the executor can open scenes, add nodes, and save.

### 6.1 Editor tools: apply immediately + timeline + Revert

- **Apply immediately**: File and node edits from tool calls run right away. Each file edit is recorded, lint runs per file after apply, dock shows status per change.
- **Tool-call contract**: Backend sends `{ "tool_name", "arguments", "output" }`. Dock uses `output` when `execute_on_client: true`; else builds payload from `tool_name` + `arguments`.
- **File/node tools**: create_file, write_file, apply_patch, create_script, delete_file, list_directory, search_files, list_files, lint_file, create_node, modify_attribute. Lint: plugin uses `tools/lint/server_lint.gd` and `lint_autofix.gd`.
- **Lint flow (so the AI always gets real error text)**:
  1. **In-editor first (GDScript)**: The plugin uses the **same pipeline as the script editor**. It registers a custom `Logger` (`tools/lint/lint_capture_logger.gd`) with `OS.add_logger()`, calls `GDScript.reload()` on the script, then `OS.remove_logger()`. The engine emits script/parse errors through the logger; we capture them (file, line, rationale) and return that text to the LLM. No subprocess, no backend—just the editor’s own linter.
  2. If the path is not `.gd` (e.g. C#) or capture returns nothing: if RAG backend URL is set, plugin POSTs to backend `/lint` (backend runs `godot --headless --editor --path <project> --script <path> --check-only` and returns stdout/stderr).
  3. If backend URL is empty, plugin runs the same Godot command in a subprocess (`GodotAIServerLint.run_lint_via_godot_subprocess`) and captures output. No third-party linter required. (Third-party options like [godot-diagnostic-list](https://github.com/mphe/godot-diagnostic-list) provide project-wide diagnostics; we don’t integrate them because we need per-file output for the AI fix flow.)
- **Lint in the same response**: After editor actions, lint runs in-editor (Logger; same as Output panel). The plugin includes lint in the same turn (edit event + chat section); no automatic follow-up request (“Lint reported errors. Requesting fix…”). See same bullet: `lint_errors_after` and "Lint after edits" block in the assistant message.
- **Local edit store** (`ai_edit_store.gd`): Persisted to `user://godot_ai_assistant_edits.json`. Holds `file_status` (path → status for indicators), `node_status` (scene → node → status), and `events` (timeline, newest first). Used for **editor indicators** and **Revert**.
- **Indicators**: File tree and script tabs show 🟢 created, 🟡 modified, ⚫ deleted, 🔴 failed (lint), by matching paths from `file_status`. Scene tree shows 🧩 created (component) and 🟡 modified for nodes in `node_status` for the open scene. See §6.5 for how decorations are applied and styling constants.
- **Timeline & Revert**: “Pending & Timeline” tab lists all applied changes (file + node) with action-type icons. Selecting a **file** event shows old vs new in the diff panel. **Revert selected** writes `old_content` back to the file and clears that path from `file_status` so the indicator goes away.

### 7.2 Edit history: plugin-only local store

- **Plugin local store** (`user://godot_ai_assistant/edit_history/edits.json`): Timeline of applied file/node changes for the **Pending & Timeline** tab, file/node status for 🟢🟡🔴 indicators, and **Revert** (writes `old_content` back to the file).
- Backend `/edit_events/*` routes are deprecated and no longer used by the plugin.

### 6.3 Dock layout

- AI dock is in `DOCK_SLOT_RIGHT_UL`; root `custom_minimum_size = Vector2(260, 220)`, `TabContainer` has `clip_contents = true`. Chat output uses word wrap; status label uses ellipsis so it doesn’t force width.

### 6.4 Action types and display

- `ai_edit_store.gd` defines action constants and `get_action_icon()` / `get_action_label()` (e.g. 📄 Add file, ✏️ Write file, 🧩 Create component). Executor returns `edit_record` with `action_type`, `summary`; file and node changes are recorded with `action_type`. Chat appends a formatted “**Editor actions**” section (icon + label + summary). Timeline shows the same icons and summaries.

### 6.5 Editor decorations (styling and discovery)

- **Module**: `editor/editor_decorator.gd` (`GodotAIEditorDecorator`) – applies AI edit indicators to the Godot editor UI (script tabs, FileSystem tree, Scene tree). Uses `GodotAIEditStore` for styling constants and status.
- **Styling constants** (`ai_edit_store.gd`): All markers are centralized so “staged” state is consistent across script tabs, FileSystem tree, and Scene tree.
  - File: `FILE_MARKER_CREATED` 🟢, `FILE_MARKER_MODIFIED` 🟡, `FILE_MARKER_DELETED` ⚫, `FILE_MARKER_FAILED` 🔴.
  - Node: `NODE_MARKER_CREATED` 🧩 (component just created), `NODE_MARKER_MODIFIED` 🟡.
  - `GodotAIEditStore.strip_markers(s)` strips any of these so decorations can be re-applied without duplicating prefixes.
- **Discovery (no brittle find_child by name)**:
  - **Script tabs**: Use `EditorInterface.get_script_editor()`, then find the TabBar under it; match each tab to `get_open_scripts()[i].resource_path` so markers use the exact script path from `file_status`.
  - **FileSystem tree**: Use `EditorInterface.get_file_system_dock()`, then find the Tree under it. No reliance on a node named `FileSystemDock` in the base control.
  - **Scene tree**: Try `SceneTreeDock` / `Scene` under base; if not found, try `EditorInterface.get_editor_main_screen()` and find `SceneTreeEditor` → Tree. Ensures markers work across different editor layouts/versions.
- **Path matching**: FileSystem tree items may store path in metadata; `_normalize_path_for_match()` normalizes slashes and converts project-absolute paths to `res://` so `file_status` keys (often `res://`) match. Fallback: suffix match on filename when metadata is not a path.
- **When decorations run**: First run is `call_deferred("_apply_editor_decorations")` so FileSystem, Script, and Scene docks exist before searching. A 1s timer refreshes decorations so new tabs/trees get markers.

### 6.6 Chat tabs and settings

- **New chat**: Creating a new chat calls `_ensure_chat_has_messages()` and `_update_context_usage_label()` so the new chat’s state and context label are in sync (no stale context from the previous chat).
- **Settings**: The main Settings UI is the dock’s **Settings tab** (logic in `ui_tabs/settings_tab.gd`; controls are in `ai_dock.tscn` under TabContainer/Settings). An optional popup panel lives in `ui_tabs/settings_panel.gd` + `ui_tabs/settings_panel.tscn` (GodotAISettingsPanel) if you need a separate modal; the primary flow uses the tab.

---

## 7. Context builder (efficient prompt assembly)

- Goal: only send what’s necessary; stable ordering; budget-aware trimming.
- Model context limits in `rag_service/app/context_builder.py` (e.g. `gpt-4.1-mini` → 32768). Blocks ordered by priority; when context fills past ~50%, lowest-priority blocks (extras) are dropped first.
- Block order: System → Current task → Retrieved session memory → Active file → Current scene scripts → Related files → Recent edits → Errors → Optional extras.
- **Conversation history**: Plugin sends `context.extra.conversation_history` (last N user/assistant turns). Backend calls `build_conversation_context()` and appends to optional extras so the model has multi-turn continuity.
- **Active file**: Plugin sends `current_script` and `extra.active_file_text`; always sends `extra.project_root_abs`. If active file text is missing, backend reads from disk.
- **Related files**: The Godot plugin may send `context.extra.related_res_paths` (one-hop `res://` dependencies). The backend embeds those files into the “Related files” context block; it does not require SQLite-based repo indexing.
- **Repair memory**: The plugin computes `context.extra.lint_repair_memory` locally (from `lint_memory_store.gd`) and injects it into the request extras; the backend includes it directly (no SQLite query).
- **Recency**: SQLite-backed recent diffs are omitted in stateless mode.
- Over-budget blocks are compressed (key symbols + head/tail) rather than randomly truncated. Backend logs `[llm_input]` for debugging.

---

## 8. Repo proximity (client-side)

- **Purpose**: Pick “nearby” project files (one-hop structural dependencies) so the backend can assemble a better context window without Chroma/SQLite.
- **Storage**: Cached JSON per project stored by the Godot plugin:
  - `godot_plugin/addons/godot_ai_assistant/core/stores/repo_index_store.gd`
  - `user://godot_ai_assistant/repo_index/<repo_id>.json`
- **Indexing**: On-demand. When a file is active, the store reads it and extracts `res://...` references, then uses those references for one-hop related selection.
- **Context flow**: The plugin injects `context.extra.related_res_paths` (list of `res://` paths). The backend embeds those files’ text into the “Related files” context block.

---

## 9. Repair memory (lint fix storage)

- **Purpose**: Store lint failure → successful fix (diff + optional explanation) so the same or similar errors get “past fix” context and the LLM produces more consistent Godot 4.x GDScript.
- **Storage**: Client-owned JSON persisted to `user://godot_ai_assistant/lint_memory/lint_memory.json` (`GodotAILintMemoryStore`). Not training the model—improving the **retrieval** layer.
- **Normalization**: Raw lint output is normalized (strip paths, line/column, quoted identifiers) and hashed with `engine_version` to form an error key so repeated identical errors collapse.
- **Endpoints**: `POST /lint_memory/record_fix` and `GET /lint_memory/search` are deprecated; the plugin no longer uses them.
- **Plugin**: When auto-lint fix succeeds, the dock records the fix locally, and injects `context.extra.lint_repair_memory` into requests so the backend can include past fixes without querying SQLite.

---

## 11. Run & test

- **Backend**: `rag_service/run_backend.ps1` (or `uvicorn app.main:app --reload`). **Tools pipeline**: `run_tools.ps1` for optional doc scraping and `rag_tests`. Quick check: `GET http://127.0.0.1:8000/health` → `{ "status": "ok" }`.

---

## 11. Implementation notes

- **Streaming + tools**: `query_stream_with_tools` streams answer, then `__TOOL_CALLS__` + JSON, then `__USAGE__`. Backend-resolved tools (read_file, list_files, list_directory, search_files, read_import_options, project_structure, find_scripts_by_extends, find_references_to when `project_root_abs` set) are already executed; only `execute_on_client: true` tools are run in Godot after the stream.
- **project_root_abs**: Plugin sends it in `context.extra`. Backend uses it for server-side file/exploration tools and for embedding the active/related file context it receives from the plugin. Open Godot from the **project folder** (e.g. `godot_plugin`), not repo root.
- **Tabs**: Tab logic uses **child node name** (e.g. `Settings`, `History`), not index, so drag-reorder works. Chat tabs: connect `active_tab_rearranged` and reorder `_chats` to match.
- **Editor decorations**: Use `EditorInterface.get_file_system_dock()` / `get_script_editor()`; match script tabs via `get_open_scripts()[i].resource_path`; `call_deferred("_apply_editor_decorations")` so docks exist. Paths: normalize to `res://`; `ProjectSettings.globalize_path("res://")` for project root.
- **GDScript 4**: No `NodePath.trim_prefix`/`path_join`—use `str(node_path).trim_prefix(...)`. No bare `_` as discard—use e.g. `var _x := ...`. Use `get_node_or_null()` for optional nodes so one bad path doesn’t block plugin load.

