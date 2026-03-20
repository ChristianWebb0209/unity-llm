# Local-first + BYOK hosted RAG rollout

## Target product shape

- **Unity plugin is the product**: user installs plugin, pastes a model provider key (BYOK), chooses a model, and uses the assistant.
- **No user accounts**: no auth, no per-user database, no cloud sync.
- **Local-first state**: chat history + checkpoints live on the user’s machine.
- **Hosted service is “stateless” w.r.t. user data**: it can run retrieval + prompt assembly + LLM calls, but does not persist user projects or chats.

## Data boundaries (what lives where)

### Client (Unity plugin)

Persist locally (OS appdata via `user://` and/or config paths):

- **Settings** (already): `unity_plugin/addons/unity_ai_assistant/settings.gd` persists to OS appdata config (`ConfigFile`).
- **Edit timeline** (already): `user://unity_ai_assistant_edits.json` in `unity_plugin/addons/unity_ai_assistant/ai_edit_store.gd`.
- **Chats** (planned): persist all chats + per-turn request/response payloads locally.
- **Checkpoints** (planned): diff-first checkpoints; when git exists, store `base_commit + patch` and fall back to file snapshots if needed.

Send to server per request (ephemeral):

- Question, minimal context (active file snippets/scene info), optional conversation window, and `chat_id` (for memory if enabled on a local backend).

### Server (`rag_service`)

Two deployment modes:

1) **Hosted, stateless wrt user data** (recommended for “install plugin + paste key”):
   - Stores only its own service data (logs/metrics if enabled).
   - Retrieval corpus is **static** (Unity docs / curated examples / tool schema), not user projects.
   - Does not store user BYOK keys (keys are used per request only).

2) **Local backend** (dev/self-host):
   - Can persist local DBs (vectors, repo index, OpenViking) on disk (already supported).

## OpenViking, repo index, and how they relate

- **OpenViking** is **per-chat memory** stored by backend when enabled:
  - Implementation: `rag_service/app/services/context/openviking_context.py`
  - Default storage: `rag_service/data/openviking/sessions/<chat_id>/...`
  - The plugin only sends `chat_id`; it does not store OpenViking memory locally today.

- **Repo indexing** is **project-scoped structural SQLite**:
  - Implementation: `rag_service/app/services/repo_indexing.py`
  - Storage: `rag_service/data/db/repo_index_<repo_id>.db`

These are separate concerns. Do not store repo index inside OpenViking.

## Vector DB choice (hosted)

If you want a lightweight hosted RAG DB without running separate infra:

- This repo no longer includes vector DB integration for hosted runtime retrieval.
- Retrieval is handled via plugin-provided active/related context (and lightweight server heuristics).
- If you add a vector DB later, it must remain stateless with respect to user projects/chats.

If you do not want any hosted DB at all:

- Bake retrieval corpus into the service image and run local embeddings/vector store, but this makes horizontal scaling harder.

## BYOK model provider integration (hosted)

Principles:

- **Do not persist provider keys** in server DB.
- Treat keys as **request-scoped secrets**:
  - read key from request (or header)
  - call provider (Together/other)
  - discard key

Operationally:

- Disable request-body logging by default.
- Add an explicit “redact secrets” layer for any debug logs.

## Local chat persistence + checkpoints (plugin) — summary requirements

### Chat persistence

- Store all chats locally in a stable, inspectable structure.
- Store **full request + response payloads per turn** (endpoint, request JSON, final answer, tool calls, usage, errors).
- Support management UX: list/search/open/export/delete.

### Checkpoints

- Diff-first with separation of concerns:
  - `GitProvider`: detect repo, get HEAD SHA, compute diffs, apply patch.
  - `CheckpointEngine`: create/restore, fallback when patch fails.
  - `CheckpointStore`: persist records under `user://`.
  - `CheckpointUI`: create/list/preview/restore/export/delete.
- When project is not a git repo: show explicit messaging and use snapshot fallback for reliability.

## Rollout plan (phased, minimal coupling)

### Phase 0 — clarify scope in docs/UI

- Update plugin settings copy: “Your chats and checkpoints are stored locally.”
- Update backend README copy: “Hosted mode does not store your project or chat history.”

### Phase 1 — ship local chat storage

- Implement chat store under `user://unity_ai_assistant/chats/`:
  - `index.json` + per-chat metadata + append-only turn files.
- Wire persistence into the chat store/controller layer (not UI).
- Add Chat History UI (separate from server-backed Edit History).

### Phase 2 — ship checkpoints (git-aware)

- Add checkpoint components (Store/Engine/GitProvider/UI).
- Start with manual checkpoint creation; restore guarded by confirmations.
- Add export/import for checkpoints (JSON + patch).

### Phase 3 — hosted BYOK RAG service (no accounts)

- Make `rag_service` accept per-request:
  - provider key
  - model id
  - question + minimal context
- Retrieval source (choose one):
  - Plugin-provided context + lightweight server heuristics (current repo behavior).
  - (Optional future work) A stateless hosted vector DB integration.
- Ensure server does not persist request payloads by default (log hygiene).

### Phase 4 — optional “local backend extras”

- For users who run backend locally:
  - enable OpenViking memory (`OPENVIKING_ENABLED=1`)
  - enable repo indexing tools for richer project navigation

## Acceptance criteria (definition of done)

- Plugin restart preserves chats exactly (tabs, titles, messages).
- Chat History UI can browse/search/export/delete without a backend running.
- Checkpoints can be created/restored; git path works when available; non-git path is explicit and reliable.
- Hosted service works with BYOK and does not retain user keys or user chats/projects.

