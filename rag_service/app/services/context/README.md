# Context services

Context building is split by concern:

| Module | Responsibility |
|--------|----------------|
| **budget** | Token estimation, per-block trim/compress, priority hierarchy, when to remove context (fill_target_ratio, drop lowest-priority blocks). |
| **scene** | Current scene: parse .tscn for script paths, load scene scripts, extract `extends` from script content. |
| **project** | Current project: read/list files, structural deps, related files (repo-index or heuristic). |
| **conversation** | Optional chat history: when the plugin sends recent turns, format them for context. |
| **openviking_context** | Per-chat session memory (OpenViking): commit turns for memory extraction, retrieve relevant memories via `find_memories` for the "Retrieved session memory" block. |
| **viewer** | Build a display model from blocks + debug for the context viewer UI (per-chat); used to send `context_view` in the response. |

**Conversation and the editor chat:** The full conversation with the AI is stored locally in the chat (editor plugin). That history is relevant to context: it gives the model dialogue continuity (what was already asked, what was suggested). To use it, the plugin can send e.g. `request_context.extra["conversation_history"]` as a list of `{"role": "user"|"assistant", "content": "..."}`. Then the server can pass it to `build_conversation_context()` and include it as a block (with a priority so it can be dropped when context fills). Not wired yet; see `conversation.build_conversation_context`.

**Orchestration:** `app.services.context.context_builder` composes these modules and exposes the same public API for `main.py` (e.g. `build_ordered_blocks`, `blocks_to_user_content`, `build_related_files_context`).
