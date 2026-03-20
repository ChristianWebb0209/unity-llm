# Context Vision (Unity Agent Working Set)

## Core Idea
Maintain a continuously updated **working set** (small, relevance-scored context window) derived from:

1. **User/editor action stream** (what the user is currently doing in Unity)
2. **Agent/tool action stream** (what the agent just changed and what failed)

Then compose the prompt from the working set with strict **budgeting + eviction**.

This is conceptually aligned with OpenViking-style memory: long-lived facts come from committed chat turns, while short-lived facts come from the working set derived from recent state changes (user and agent).

---

## 1) Component / Responsibility Map (UML-style)

```mermaid
classDiagram
direction TB

class EditorActionListener{
  +onActiveScriptChanged(path)
  +onSelectionChanged(nodePath, nodeClass)
  +onSceneOpened(sceneResPath)
  +onResourceOpened(resPath)
  +onOpenTabsChanged(topNPaths)
  +onPinnedContextChanged(items)
  +onExcludeContextChanged(keys)
}

class AgentActionListener{
  +onToolExecuted(toolCall)
  +onEditApplied(editRecord)
  +onLintFailed(filePath, lintOutput)
  +onNodeCreated(scenePath, nodePath)
  +onNodePropertyModified(scenePath, nodePath, propName)
  +onRunSceneResult(scenePath, logs)
}

class ContextIndex{
  +addCandidate(candidate)
  +updateCandidate(id, fields)
  +decayByTTL(now)
  +evictLowScoreUntilBudgetFit()
  +getWorkingSet(maxTokens)
}

class Candidate{
  +id
  +type "active_file|scene_scripts|selection|pinned|recent_edits|errors|recent"
  +source "user|agent"
  +pathOrId
  +textPayload
  +score
  +ttl
  +tokenCostEstimate
  +tags
}

class RelevanceScorer{
  +score(candidate, currentState, lastTurnOutcome)
}

class ContextBudgeter{
  +tokenBudget(model)
  +fitWorkingSet(candidates, budget)
}

class ContextBuilder{
  +buildContextBlocks(workingSet)
  +buildPrompt(contextBlocks, userIntent)
}

class BackendClient{
  +queryWithContext(prompt, schemaMode)
  +streamWithTools(prompt, schemaMode)
}

class ToolRunner{
  +executeToolCalls(toolCalls)
  +applyEdits()
  +triggerLintIfNeeded()
}

class EditStore{
  +recordEditRecords()
  +getRecentEdits(N)
  +getPendingVsAppliedTimeline()
  +getHistory()
}

class OpenVikingMemory{
  +find_memories(chatId, query)
  +add_turn_and_commit(chatId, messages)
}

EditorActionListener --> ContextIndex : generates candidates
AgentActionListener --> ContextIndex : generates candidates
ContextIndex --> RelevanceScorer : provides candidates to score
RelevanceScorer --> ContextIndex : updates scores
ContextIndex --> ContextBudgeter : budget-fit selection
ContextBudgeter --> ContextBuilder : returns working set
ContextBuilder --> BackendClient : prompt composition
BackendClient --> ToolRunner : tool calls (when enabled)
ToolRunner --> EditStore : edit records
ToolRunner --> AgentActionListener : tool outcomes / errors
ContextBuilder --> OpenVikingMemory : (optional) retrieve session memory
OpenVikingMemory --> ContextBuilder : session memory snippets
ContextBuilder --> BackendClient : final context + instructions
ToolRunner --> AgentActionListener : lint failures + outputs
```

---

## 2) Two Action Streams -> Continuously Updated Working Set

```mermaid
flowchart TD
  A[Unity Editor: user events] --> B[EditorActionListener]
  C[Agent/tool execution events] --> D[AgentActionListener]

  B --> E[ContextIndex (working candidates)]
  D --> E

  E --> F[RelevanceScorer (score + TTL decay)]
  F --> G[ContextBudgeter (token fit + eviction)]
  G --> H[ContextBuilder (blocks: active, scene, selection, errors, recent edits)]
  H --> I[Backend Client (stream or non-stream)]
  I --> J[ToolRunner (execute + lint follow-up triggers)]
  J --> D

  %% OpenViking: longer-term memory retrieval
  K[OpenViking (session memory)] --- H : retrieved_memories (when available)
  J --> L[OpenViking commit (fire-and-forget)]
```

### Candidate types (conceptual examples)
- `active_file`: the user's currently open/edited script content (bounded, truncated, or summarized)
- `selection`: the selected node class/path and relevant hints for modifications
- `scene_scripts`: scripts attached to nodes in the currently open scene (and optionally their base-class extracts)
- `pinned_context`: user-curated context items
- `recent_edits`: agent-caused changes (file paths, diffs, node paths)
- `errors`: lint/parser/compiler diagnostics tied to file paths + failing lines
- `recent_user_activity`: open tabs, recently accessed resources

### Lazy inclusion ("send only what matters")
- Project-wide blobs (autoloads, input maps, keybindings) are often *expensive*.
- Prefer including them only when relevance signals indicate they matter.

---

## 3) Ask Turn Lifecycle (Sequence Diagram)

```mermaid
sequenceDiagram
  actor User
  participant L as EditorActionListener
  participant A as AgentActionListener
  participant CI as ContextIndex
  participant CB as ContextBuilder
  participant BC as BackendClient
  participant TR as ToolRunner

  User->>L: change selection / open script / pin context / request action
  L->>CI: add/update candidates (user stream)

  Note over CI: ContextIndex maintains TTL + score + eviction.

  User->>CB: compose request for current chat turn
  CB->>CI: getWorkingSet(maxTokens)
  CB->>BC: send prompt (with working-set blocks)
  BC-->>User: stream answer text (optional)
  BC-->>TR: tool calls payload (when tool-enabled mode is active)

  TR->>TR: execute tools + apply edits + trigger lint
  TR->>A: emit agent events (editRecord, lint failures, run results)
  A->>CI: update candidates (agent stream)

  Note over CB: Next turn can drop stale context and add new hot facts.
```

---

## OpenViking Integration (Where it fits conceptually)
- **Retrieve**: when composing the next prompt, fetch relevant session memory snippets for the chat id, and include them as a bounded block (e.g. `session_memory`).
- **Commit**: after the assistant responds (and/or after each tool round), add the user+assistant messages to OpenViking so it can extract longer-lived facts.

OpenViking is complementary:
- working set = short-lived, stateful facts derived from editor+tool events
- OpenViking memory = longer-lived, semantic summaries derived from committed turns

---

## Why this matters for a Unity agent (fundamentals)
In Unity, context is not just "which files exist":
- The model needs a representation of the current engine world state (scene + attached scripts + selection + relevant wiring).
- Tool execution changes the world; those changes must become context immediately.
- Context must be evicted/decayed so the model does not "fight" stale assumptions.

This design optimizes:
- continuous focus via eviction + TTL + scoring
- two-sided causality (user intent vs agent outcome)
- on-demand inclusion of expensive project-wide facts
- reliability loops: errors and edit records become the next turn's highest-signal context

