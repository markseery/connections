# Architecture Proposals — Request/Response Standardization

Date: 2026-03-18

## Current Request Lifecycle

```
Browser (agent_chat.js)
  │  POST {agent_chat_url}/chat  { namespace, prompt }
  ▼
agent_chat server (7025)
  │  load/create current_memory from storage
  │  POST {agent_url}/agent/execute { prompt, conversation_context }
  ▼
agent server (7024)
  │  discover_skills() via configuration server
  │  use_skills_high_confidence() → AI call (router prompt)
  │  create_plan()                 → AI call (planner prompt)
  │  execute_plan()                → HTTP to worker skill routes
  ▼
worker (7030/7031)
  │  SkillManager.load() → import skills.{name} → mount router
  │  skill endpoint runs, returns dict
  ▼
agent server
  │  _build_answer() → raw text: "Objective: ...\nResults:\n  - skill path: {json}"
  │  returns AgentExecutionResult
  ▼
agent_chat server
  │  _build_display_from_result() → skill_response_to_markdown() per step
  │  OR _format_raw_answer_with_results() → parse raw text, extract JSON, format
  │  OR _format_answer_for_display() → detect single JSON blob, format
  │  appends to memory, returns { namespace, prompt, text, profile, provider }
  ▼
Browser
  │  marked.parse(data.text) + DOMPurify → render
```

## Issues Identified

### 1. Two parallel chat servers doing the same thing differently

`agent_chat` (7025) and `chatagent` (7023) both plan+execute skills and fall back to AI.
They share the planner and executor code but diverge on request/response shapes, memory,
and formatting.

### 2. No common request/response contract

Every server defines its own ad-hoc response dict:
- `agent_chat` returns `{ text }` (markdown baked in)
- `chatagent` returns `{ output: { text } }` plus `{ raw }` (structured data available)
- `agent/execute` returns `{ result: AgentExecutionResult }` (Pydantic model)
- Skills return arbitrary dicts

An API consumer gets markdown (meant for the UI) with no way to get structured JSON.

### 3. Formatting is coupled into the wrong layer

`agent_chat/routes.py` has ~100 lines of display formatting embedded in route handlers.
Formatted markdown is also saved into memory, polluting conversation context with
presentation artifacts instead of clean data.

### 4. The agent "answer" is a lossy text serialization

`service.py::_build_answer` serializes structured `StepResult` objects into raw text
(`"Objective: ...\nResults:\n  - skill path: {json}"`), which `agent_chat` then
re-parses to extract JSON blobs. This is fragile.

### 5. Synchronous blocking with no streaming

The full pipeline (router AI call + planner AI call + skill execution) runs synchronously.
With a 180-second timeout the UI shows a spinner for the entire duration.

### 6. httpx.Client created per-call (no connection reuse)

Every function creates a new `httpx.Client(...)` in a `with` block. A single request
creates 5+ TCP connections that are immediately discarded.

### 7. Registry lookup on every request

`_agent_url()` and `_storage_url()` hit the registry server on every chat request.
These URLs don't change at runtime.

### 8. SkillLifecycle.prepare() runs on every chatagent request

Scans the filesystem, loads skills, and registers configs on every `POST /chat`.

### 9. Skill response shape is documented but not enforced

`skill_response.py` documents the canonical shape but skills return arbitrary dicts.
The normalizer handles some variants but structured data like stock quotes falls through
to a generic key-value dump.

### 10. No error envelope

Errors surface as HTTP status codes with `detail` strings, `{ "error": "..." }`, or
`{ "success": false, "step_results": [...] }`. No consistent error shape.

---

## Proposals (priority order)

### P1: Common Response Envelope

**Impact:** High — unlocks dual UI/API use  
**Effort:** Medium  
**Files:** `common/models.py` (new), all servers, all skills

Define one Pydantic model used by all servers:

```python
class SkillOutput(BaseModel):
    summary: str = ""
    items: list[dict[str, Any]] = []
    text: str = ""
    data: dict[str, Any] = {}        # full structured payload

class ServiceResponse(BaseModel):
    success: bool = True
    prompt: str | None = None
    namespace: str | None = None
    output: SkillOutput
    source: str = ""                  # "skill:news_skill", "ai:agent", etc.
    error: str | None = None
    metadata: dict[str, Any] = {}     # timing, plan_cache_hit, request_id, etc.
```

- Skills populate `SkillOutput`.
- Servers return `ServiceResponse`.
- UI renders via `skill_response_to_markdown(response.output)` client-side.
- API consumers use the structured `output.data` directly.

### P2: Eliminate Text Serialization Round-Trip

**Impact:** High — removes fragile parsing  
**Effort:** Low  
**Files:** `servers/agent/service.py`, `servers/agent_chat/routes.py`

Stop converting structured `StepResult` objects into a text string in `_build_answer`.
Return the structured `step_results` array directly in `AgentExecutionResult`. Remove
the text-parsing formatters (`_format_raw_answer_with_results`). The `answer` field
should be the AI's natural-language synthesis if desired, not a text dump of JSON.

### P3: Separate Data from Presentation

**Impact:** High — clean memory, API-ready  
**Effort:** Medium  
**Files:** `servers/agent_chat/routes.py`, `common/skill_response.py`, UI JS

1. Stop baking markdown into the server response. Return `ServiceResponse` with
   structured `SkillOutput`.
2. Move `skill_response_to_markdown` to the UI layer (client-side JS or a
   `/format` endpoint).
3. Store clean data in memory — `"User asked for CRWV stock. Price: $42.50,
   change: +2.3%"` — not formatted markdown.

### P4: Consolidate agent_chat and chatagent

**Impact:** Medium — less code, one contract  
**Effort:** Medium  
**Files:** `servers/agent_chat/`, `servers/chatagent/`, `servers/connections_ui/`

Merge into one server with:
- Memory support opt-in via `namespace` (omit for stateless).
- One code path for planning + execution.
- Returns `ServiceResponse` (P1).
- UI formats client-side (P3).

### P5: Cache Registry Lookups + Reuse HTTP Clients

**Impact:** Medium — performance  
**Effort:** Low  
**Files:** `servers/agent_chat/routes.py`, `servers/agent/config.py`, `servers/chatagent/config.py`

- Cache registry results at module level (invalidate on 502/connection error).
- Use a module-level or lifespan-managed `httpx.Client` with connection pooling
  instead of creating one per call.

### P6: Enforceable Skill Response Shape

**Impact:** Medium — extensibility  
**Effort:** Low  
**Files:** `common/skill_response.py`, individual skills

Provide a helper that validates and returns a canonical response:

```python
def skill_result(summary="", items=None, text="", **data) -> dict[str, Any]:
    return SkillOutput(summary=summary, items=items or [], text=text, data=data).model_dump()
```

Skills use `return skill_result(summary="...", items=[...])`. The normalizer becomes
unnecessary over time as skills adopt the helper.

### P7: SkillLifecycle.prepare() at Startup

**Impact:** Medium — performance  
**Effort:** Low  
**Files:** `servers/chatagent/routes.py`, `servers/agent/service.py`

Run `prepare()` once at startup (or on first request then cache the result).
Don't scan the filesystem and load skills on every chat request.

### P8: Standardize Error Responses

**Impact:** Medium — debuggability  
**Effort:** Low  
**Files:** All servers

Define an error envelope:

```python
class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str = ""            # machine-readable: "skill_not_found", "ai_timeout"
    detail: dict[str, Any] = {}
```

Return this (with appropriate HTTP status) from all servers.

### P9: SSE Streaming for Chat

**Impact:** High — UX  
**Effort:** High  
**Files:** `servers/agent_chat/routes.py`, UI JS, possibly agent server

Add SSE or chunked responses:
1. "Planning..." (after router decides to use skills)
2. "Executing step 1/3: news_skill..." (during execution)
3. Final result

FastAPI supports `StreamingResponse` and SSE natively.

### P10: Enforce Skill Request Models

**Impact:** Low — already mostly done  
**Effort:** Low  
**Files:** Individual skills

All skills should use Pydantic request models (most already do). The planner
generates `arguments` that map to these models; having a schema means the planner
can be given parameter names and types for better accuracy.

---

## Implementation Status

| # | Proposal | Status | Files Changed |
|---|----------|--------|---------------|
| P1 | Common response envelope | **Done** | `common/models.py` (new), `common/__init__.py` |
| P2 | Eliminate text serialization | **Done** | `servers/agent/service.py` |
| P3 | Separate data from presentation | **Done** | `servers/agent_chat/routes.py`, `servers/chatagent/routes.py` |
| P4 | Consolidate chat servers | **Done** | `servers/chat/` (new); `/chat` (skill+AI) + `/agent-chat` (memory+agent) on port 7023 |
| P5 | Cache registry + reuse clients | **Done** | `common/registry_client.py` (new), all `config.py` files, `agent_chat/routes.py` |
| P6 | Enforceable skill response | **Done** | `common/models.py` (`skill_result`), `common/skill_response.py` (re-export) |
| P7 | Lifecycle at startup | **Done** | `servers/chatagent/routes.py` |
| P8 | Standardize errors | **Done** | `common/models.py` (`ErrorDetail`), both chat servers |
| P9 | SSE streaming | Deferred | High effort; do as separate PR |
| P10 | Enforce skill request models | **Done** | `news_skill.SearchRequest`, `webscraper_skill.SummarizeTextRequest`, `workflow_skill.CreateTemplateRequest` + `ExecuteWorkflowRequest` |
