# Autonomous Agents: Architecture, Design & Implementation

## 1. Overview

This document defines the architecture for autonomously running agents within
Connections. An autonomous agent operates without continuous human prompting —
it receives a goal, decomposes it into subtasks, executes them using skills and
AI, handles failures, and delivers a result. A **supervisor agent** manages the
lifecycle of one or more **subagents**, each specialised for a domain.

### Design Principles

1. **Agents are systems, not prompts.** The LLM is the reasoning engine; the
   architecture provides planning, execution, memory, safety, and observability.
2. **Calibrated autonomy.** Full autonomy for reversible, low-risk actions;
   human approval for irreversible or high-cost actions.
3. **Structured everything.** Structured logging (JSONL), structured tool
   contracts (Pydantic), structured memory (layered, typed).
4. **Extend, don't rewrite.** Leverage existing servers (agent, workflow,
   storage, aiserver), models (`SkillOutput`, `ServiceResponse`), and
   infrastructure (`http_client`, `SkillConfig`, `AdaptivePollDelay`).

---

## 2. Architecture

### 2.1 Component Topology

```
┌─────────────────────────────────────────────────────────┐
│                     Supervisor Agent                     │
│  ┌──────────┐  ┌────────────┐  ┌─────────────────────┐ │
│  │ Goal     │  │ Subagent   │  │ Approval Gate       │ │
│  │ Planner  │  │ Lifecycle  │  │ Manager             │ │
│  └──────────┘  └────────────┘  └─────────────────────┘ │
│  ┌──────────┐  ┌────────────┐  ┌─────────────────────┐ │
│  │ Context  │  │ Memory     │  │ Structured Logger   │ │
│  │ Manager  │  │ Manager    │  │ (JSONL)             │ │
│  └──────────┘  └────────────┘  └─────────────────────┘ │
└───────┬──────────────┬──────────────────┬───────────────┘
        │              │                  │
   ┌────▼────┐   ┌─────▼─────┐    ┌──────▼──────┐
   │Subagent │   │ Subagent  │    │  Subagent   │
   │Research │   │ Execute   │    │  Notify     │
   └────┬────┘   └─────┬─────┘    └──────┬──────┘
        │              │                  │
   ┌────▼──────────────▼──────────────────▼──────┐
   │           Existing Infrastructure            │
   │  Skills · AIServer · Storage · Registry      │
   │  Workers · Workflow Server · HTTP Client      │
   └──────────────────────────────────────────────┘
```

### 2.2 Reused Components

| Existing Component | Role in Autonomous Agents |
|---|---|
| `servers/agent/planner.py` | Base planning logic — extended for goal decomposition |
| `servers/agent/executor.py` | Step execution with dependency waves — reused by subagents |
| `servers/agent/router.py` | Skills-vs-AI routing — reused per subagent |
| `servers/agent/context.py` | `AgentContext` scratchpad — extended with memory layers |
| `servers/agent/models.py` | `AgentPlan`, `StepResult`, `AgentJobState` — extended |
| `servers/workflow/executor.py` | Multi-step YAML execution — used for structured workflows |
| `common/http_client.py` | Instrumented httpx — all agent HTTP calls go through this |
| `common/adaptive_poll.py` | `AdaptivePollDelay` — supervisor polls subagent jobs |
| `common/models.py` | `SkillOutput`, `ServiceResponse` — canonical response shapes |
| `common/skill_config.py` | `SkillConfig` — per-agent YAML configuration |
| `servers/storage/` | Persistent key-value store — backs memory and job persistence |
| `app_config.yaml` | Server topology — new `autonomous` server entry |

---

## 3. Supervisor Agent

### 3.1 Responsibilities

The supervisor agent is the entry point for autonomous goals. It:

1. **Receives a goal** — a high-level objective (e.g., "Monitor CoreWeave news
   daily and email me a summary").
2. **Decomposes** into subgoals using the planner with a supervisor-specific
   planning prompt.
3. **Spawns subagents** — each subgoal becomes a subagent with its own plan,
   context, and execution.
4. **Monitors** subagent progress via polling (using `AdaptivePollDelay`).
5. **Handles failures** — retries, replans, or escalates to human.
6. **Aggregates results** — collects subagent outputs, compacts context, and
   produces a final response.

### 3.2 Supervisor Loop

```python
class SupervisorAgent:
    def run(self, goal: str, config: AgentConfig) -> SupervisorResult:
        # 1. Plan
        plan = self.planner.decompose_goal(goal, self.memory.context_window())

        # 2. Check approval gates
        for step in plan.steps:
            if self.approval_gate.requires_approval(step):
                self.approval_gate.request_approval(step)
                # Pause until approved or rejected

        # 3. Spawn subagents
        subagent_jobs = []
        for subgoal in plan.subgoals:
            job_id = self.spawn_subagent(subgoal)
            subagent_jobs.append(job_id)

        # 4. Monitor
        results = self.monitor_subagents(subagent_jobs)

        # 5. Compact and store memory
        self.memory.compact_and_store(goal, plan, results)

        # 6. Aggregate
        return self.aggregate_results(results)
```

### 3.3 Data Model

```python
class SupervisorGoal(BaseModel):
    goal_id: str
    goal: str
    config: AgentConfig
    status: Literal["planning", "awaiting_approval", "running",
                     "completed", "failed"]
    subagent_ids: list[str] = []
    created_at: str
    completed_at: str | None = None

class AgentConfig(BaseModel):
    """Per-goal configuration loaded from config/agents/<agent_name>.yaml"""
    max_subagents: int = 5
    max_steps_per_subagent: int = 10
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    memory_layers: list[str] = ["working", "episodic", "semantic"]
    timeout: float = 3600.0
    context_window_limit: int = 32000  # tokens
```

### 3.4 Configuration

File: `config/agents/supervisor.yaml`

```yaml
max_subagents: 5
max_steps_per_subagent: 10
max_replan_attempts: 3
approval_policy: approve_irreversible  # auto | approve_all | approve_irreversible
timeout: 3600
context_window_limit: 32000
memory:
  working_ttl: 3600
  episodic_max_entries: 100
  compaction_threshold: 20000  # compact when working memory exceeds this (tokens)
logging:
  level: info
  file: logs/supervisor.jsonl
```

---

## 4. Subagents

### 4.1 Design

A subagent is a scoped instance of the existing `AgentService` with:
- Its own `AgentContext` (scratchpad)
- A subset of available skills (scoped by the supervisor)
- A working memory slice from the supervisor
- An execution budget (max steps, timeout)

Subagents reuse the existing plan → execute → replan loop from
`servers/agent/service.py`. The key extension is that subagents report
progress back to the supervisor via the job store.

### 4.2 Subagent Types

Subagent types are defined by configuration, not code. Each type specifies
which skills it can access and its planning prompt.

File: `config/agents/research_subagent.yaml`

```yaml
description: "Fetches and synthesises information from web, RSS, and stored content."
skills:
  - news_skill
  - rss_new_skill
  - webscraper_skill
planning_prompt_addendum: >
  You are a research agent. Your goal is to find, fetch, and synthesise
  information. Do NOT send emails or modify data. Return structured findings.
max_steps: 10
timeout: 600
```

File: `config/agents/notify_subagent.yaml`

```yaml
description: "Sends notifications via email."
skills:
  - notification_skill
planning_prompt_addendum: >
  You are a notification agent. You compose and send emails based on
  content provided to you. Do not fetch or research — only format and send.
max_steps: 3
timeout: 120
approval_required: true  # always requires approval before sending
```

### 4.3 Subagent Lifecycle

```
CREATED → PLANNING → EXECUTING → COMPLETED
                  ↘             ↗
                   → FAILED ────
                   → AWAITING_APPROVAL → EXECUTING
```

### 4.4 Inter-Agent Communication

Subagents communicate via the supervisor's scratchpad, not directly:

```python
class SupervisorScratchpad:
    """Extended AgentContext for multi-agent coordination."""

    def __init__(self):
        self.subagent_outputs: dict[str, Any] = {}  # subagent_id → result
        self.shared_context: dict[str, str] = {}     # key → value

    def publish(self, subagent_id: str, key: str, value: Any) -> None:
        self.subagent_outputs[subagent_id] = value
        self.shared_context[key] = value

    def read(self, key: str) -> Any:
        return self.shared_context.get(key)
```

A downstream subagent can reference upstream outputs via `$agent.<id>.<path>`
(extending the existing `$step.<id>.<path>` pattern).

---

## 5. Structured Logging

### 5.1 Rationale

OpenTelemetry adds significant infrastructure overhead (collector, backend,
query UI). Structured JSONL logging achieves the same goals — traceability,
analytics, debugging — with zero additional infrastructure. Every log line
is a self-contained JSON object written to an append-only file.

### 5.2 Log Schema

All agent logs share a common envelope:

```json
{
  "ts": "2026-03-22T12:34:56.789Z",
  "level": "info",
  "component": "supervisor",
  "goal_id": "g-abc123",
  "subagent_id": null,
  "event": "plan_created",
  "data": {
    "objective": "Monitor CoreWeave news",
    "steps": 3,
    "elapsed_ms": 1423
  }
}
```

### 5.3 Event Types

| Event | Component | Description |
|---|---|---|
| `goal_received` | supervisor | New goal submitted |
| `plan_created` | supervisor/subagent | Plan generated with step count |
| `approval_requested` | approval_gate | Action requires human approval |
| `approval_granted` | approval_gate | Human approved action |
| `approval_denied` | approval_gate | Human denied action |
| `subagent_spawned` | supervisor | Subagent created with config |
| `step_started` | subagent | Skill/AI step begins |
| `step_completed` | subagent | Step finished with status and duration |
| `step_failed` | subagent | Step failed with error |
| `replan_triggered` | subagent | Replanning after failure |
| `memory_compacted` | memory_manager | Working memory summarised |
| `context_window_usage` | context_manager | Token count snapshot |
| `goal_completed` | supervisor | All subagents finished |
| `goal_failed` | supervisor | Goal failed after retries |

### 5.4 Implementation

Extend the existing `common/http_client.py` JSONL logging pattern:

```python
# common/agent_logger.py

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path("./logs")
_lock = threading.Lock()


class AgentLogger:
    def __init__(self, component: str, log_file: str = "agent.jsonl"):
        self._component = component
        self._path = _LOG_DIR / log_file
        _LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, *, level: str = "info",
            goal_id: str | None = None,
            subagent_id: str | None = None,
            **data: Any) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": self._component,
            "goal_id": goal_id,
            "subagent_id": subagent_id,
            "event": event,
            "data": data,
        }
        line = json.dumps(entry, default=str) + "\n"
        with _lock:
            with open(self._path, "a") as f:
                f.write(line)
```

### 5.5 Log Files

| File | Contents |
|---|---|
| `logs/agent.jsonl` | All agent events (supervisor + subagents) |
| `logs/http_calls.jsonl` | All HTTP calls (existing, from `http_client.py`) |
| `logs/approvals.jsonl` | Approval requests and decisions |

---

## 6. Layered Memory

### 6.1 Memory Architecture

```
┌─────────────────────────────────────────────┐
│              Memory Manager                  │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │ Working  │  │ Episodic │  │ Semantic  │ │
│  │ Memory   │  │ Memory   │  │ Memory    │ │
│  │          │  │          │  │           │ │
│  │ Current  │  │ Past run │  │ Facts,    │ │
│  │ goal,    │  │ summaries│  │ prefs,    │ │
│  │ scratch  │  │ & lessons│  │ patterns  │ │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘ │
│       │              │              │        │
│       └──────────────┼──────────────┘        │
│                      │                       │
│              Storage Server (7010)           │
│              Namespace: agent_memory         │
└──────────────────────────────────────────────┘
```

### 6.2 Memory Layers

**Working Memory** — current goal context. Lives in-memory during a run,
persisted to storage on completion or interruption.

```python
class WorkingMemory:
    goal: str
    plan: AgentPlan | None
    scratchpad: dict[str, Any]        # step outputs
    conversation: list[dict[str, str]] # role/content pairs
    token_count: int                   # estimated tokens used
```

**Episodic Memory** — summaries of past runs. Each completed goal produces a
compact episode stored in the storage server.

```python
class Episode:
    goal_id: str
    goal_summary: str        # one-line goal
    outcome: str             # "completed" | "failed" | "partial"
    key_findings: list[str]  # bullet points from the run
    skills_used: list[str]
    duration_seconds: float
    timestamp: str
```

**Semantic Memory** — persistent facts, user preferences, and learned patterns.
Extracted from episodes via AI summarisation.

```python
class SemanticEntry:
    key: str          # e.g., "user_preference:email_format"
    value: str        # e.g., "modern minimalistic HTML"
    source_goal_id: str
    confidence: float # 0.0–1.0
    updated_at: str
```

### 6.3 Storage Layout

All memory is stored in the existing storage server under dedicated namespaces:

| Namespace | Key Pattern | Value |
|---|---|---|
| `agent_memory_working` | `{agent_id}:{goal_id}` | `WorkingMemory` JSON |
| `agent_memory_episodic` | `{agent_id}:{goal_id}` | `Episode` JSON |
| `agent_memory_semantic` | `{agent_id}:{key}` | `SemanticEntry` JSON |

### 6.4 Context Window for Planning

When the supervisor or subagent calls the planner, the context window is
assembled from memory layers:

```python
def context_window(self) -> str:
    parts = []

    # Semantic: always included (small, high-value)
    semantic = self.semantic_memory.get_relevant(self.current_goal, limit=10)
    if semantic:
        parts.append("## Known Facts\n" + "\n".join(f"- {s.value}" for s in semantic))

    # Episodic: recent relevant episodes
    episodes = self.episodic_memory.get_recent(limit=5)
    if episodes:
        parts.append("## Recent Experience\n" + "\n".join(
            f"- [{e.outcome}] {e.goal_summary}: {'; '.join(e.key_findings[:3])}"
            for e in episodes
        ))

    # Working: current scratchpad (compacted if over threshold)
    working = self.working_memory.to_text()
    if self._estimate_tokens(working) > self.config.compaction_threshold:
        working = self._compact(working)
    parts.append("## Current Context\n" + working)

    return "\n\n".join(parts)
```

---

## 7. Approval Gates

### 7.1 Policy Model

```python
class ApprovalPolicy(str, Enum):
    AUTO = "auto"                          # no approvals needed
    APPROVE_ALL = "approve_all"            # everything needs approval
    APPROVE_IRREVERSIBLE = "approve_irreversible"  # only irreversible actions
```

### 7.2 Action Classification

Each skill route is tagged with a risk level in its configuration:

File: `config/skills/notification_skill.yaml`

```yaml
# ... existing config ...
route_risk:
  "POST /skills/notification_skill/send": irreversible
  "POST /skills/notification_skill/send/test": irreversible
  "GET /skills/notification_skill/notifications": safe
  "GET /skills/notification_skill/config": safe
  "GET /skills/notification_skill/stats": safe
```

File: `config/skills/webscraper_skill.yaml`

```yaml
# ... existing config ...
route_risk:
  "POST /skills/webscraper_skill/scrape": reversible
  "POST /skills/webscraper_skill/pages": reversible
  "PUT /skills/webscraper_skill/pages": reversible
  "DELETE /skills/webscraper_skill/pages": irreversible
  "GET *": safe
```

Default: if a route is not listed, `POST/PUT/DELETE` = `reversible`,
`GET` = `safe`.

### 7.3 Approval Flow

```
Agent plans step with irreversible action
    │
    ▼
ApprovalGate.check(step) → is policy "approve_irreversible"?
    │                              │
    │ no                           │ yes
    ▼                              ▼
Execute                     Create ApprovalRequest
                                   │
                                   ▼
                            Store in approval queue
                            Log to approvals.jsonl
                            Set step status = "awaiting_approval"
                                   │
                                   ▼
                            Human reviews via UI or API:
                              GET  /approvals/pending
                              POST /approvals/{id}/approve
                              POST /approvals/{id}/deny
                                   │
                            ┌──────┴──────┐
                            │             │
                         approved       denied
                            │             │
                            ▼             ▼
                         Execute     Skip step,
                         action      log denial,
                                     continue or
                                     fail goal
```

### 7.4 Approval Data Model

```python
class ApprovalRequest(BaseModel):
    approval_id: str
    goal_id: str
    subagent_id: str | None
    step: PlannedStep
    risk_level: str              # "irreversible", "reversible"
    action_description: str      # human-readable summary
    status: Literal["pending", "approved", "denied", "expired"]
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    ttl_seconds: int = 3600      # auto-deny after this
```

### 7.5 Approval Storage

Stored in the storage server under namespace `agent_approvals`.
Key: `{approval_id}`. Listed via `GET /namespaces/agent_approvals/records`.

---

## 8. Context Compaction

### 8.1 Problem

Long-running agents accumulate context — step outputs, intermediate results,
conversation history. LLM reasoning quality degrades when context exceeds
~32K tokens. Without compaction, a 20-step agent run can exceed this.

### 8.2 Strategy

Compaction runs automatically when `working_memory.token_count` exceeds
`config.compaction_threshold`. It uses the AI server to summarise older
context while preserving recent steps.

```python
class ContextCompactor:
    def compact(self, working_memory: WorkingMemory,
                threshold: int = 20000) -> WorkingMemory:
        if working_memory.token_count <= threshold:
            return working_memory

        # Split: keep last 3 steps verbatim, summarise the rest
        recent_steps = working_memory.conversation[-3:]
        older_steps = working_memory.conversation[:-3]

        summary = self._summarise_via_ai(older_steps)
        compacted_entry = {
            "role": "system",
            "content": f"[Compacted context summary]\n{summary}"
        }

        working_memory.conversation = [compacted_entry] + recent_steps
        working_memory.token_count = self._estimate_tokens(
            working_memory.conversation
        )
        self.logger.log("memory_compacted",
                        goal_id=working_memory.goal_id,
                        old_tokens=working_memory.token_count,
                        new_tokens=working_memory.token_count)
        return working_memory

    def _summarise_via_ai(self, entries: list[dict]) -> str:
        text = "\n".join(e["content"] for e in entries)
        # Truncate to fit AI context
        text = text[:30000]
        prompt = (
            "Summarise the following agent execution context into a concise "
            "paragraph. Preserve key facts, decisions, results, and any "
            "values that downstream steps may need. Be terse.\n\n"
            f"{text}"
        )
        with http_client("ai_generate") as client:
            r = client.post(f"{aiserver_url}/generate",
                          json={"prompt": prompt, "profile": "fast"})
            r.raise_for_status()
            return r.json().get("output", {}).get("text", "")
```

### 8.3 When Compaction Runs

| Trigger | Action |
|---|---|
| Before planning | Compact if working memory > threshold |
| After each step | Check token count, compact if needed |
| On goal completion | Compact working → episodic (full summary) |
| On episodic overflow | Oldest episodes → semantic extraction |

---

## 9. Implementation Plan

### Phase 1: Foundation (agent logger, memory manager, approval model)

| Task | Files | Depends On |
|---|---|---|
| Create `common/agent_logger.py` | New | — |
| Create `common/agent_memory.py` (memory manager) | New | Storage server |
| Create `common/approval_gate.py` (approval model + gate) | New | Storage server |
| Create `config/agents/` directory with supervisor.yaml | New | `SkillConfig` pattern |
| Add `route_risk` entries to skill config YAMLs | Existing configs | — |
| Extend `servers/agent/models.py` with supervisor models | Existing | — |

### Phase 2: Supervisor Agent

| Task | Files | Depends On |
|---|---|---|
| Create `servers/autonomous/` server package | New | Phase 1 |
| Implement `SupervisorAgent` (goal decompose, spawn, monitor) | New | Planner, Memory |
| Implement subagent spawning (scoped `AgentService` instances) | New | AgentService |
| Wire `AdaptivePollDelay` for subagent monitoring | Existing | — |
| Add `autonomous` server to `app_config.yaml` | Existing | — |

### Phase 3: Context Compaction & Episodic Memory

| Task | Files | Depends On |
|---|---|---|
| Implement `ContextCompactor` | New module in `common/` | AIServer |
| Wire compaction into subagent execution loop | `servers/agent/service.py` | Compactor |
| Implement episodic memory persistence (goal → episode) | `common/agent_memory.py` | Storage |
| Implement semantic extraction (episodes → facts) | `common/agent_memory.py` | AIServer |

### Phase 4: Approval Gates

| Task | Files | Depends On |
|---|---|---|
| Add approval API routes to autonomous server | `servers/autonomous/routes.py` | Phase 2 |
| Wire `ApprovalGate.check()` into executor before step dispatch | `servers/agent/executor.py` | Gate model |
| Add approval UI to connections_ui | `servers/connections_ui/` | Approval API |

### Phase 5: Production Hardening

| Task | Files | Depends On |
|---|---|---|
| Persist job store to storage server (survive restarts) | `servers/agent/service.py` | Storage |
| Migrate all agent HTTP calls to `http_client` | `servers/agent/executor.py` | — |
| Add budget enforcement (max steps, max cost, max time) | Supervisor + subagents | — |
| Add CI evaluation harness (test full agent trajectories) | `tests/` | — |

---

## 10. Server Topology (Target State)

```yaml
# app_config.yaml additions
  - name: autonomous
    app: servers.autonomous.main:app
    host: 127.0.0.1
    port: 7027
```

### API Surface

| Method | Path | Description |
|---|---|---|
| `POST` | `/goals/submit` | Submit a new autonomous goal |
| `GET` | `/goals/{goal_id}` | Get goal status and subagent progress |
| `GET` | `/goals` | List all goals |
| `POST` | `/goals/{goal_id}/cancel` | Cancel a running goal |
| `GET` | `/approvals/pending` | List pending approval requests |
| `POST` | `/approvals/{id}/approve` | Approve an action |
| `POST` | `/approvals/{id}/deny` | Deny an action |
| `GET` | `/memory/{agent_id}/episodes` | List episodic memory |
| `GET` | `/memory/{agent_id}/semantic` | List semantic memory |
| `DELETE` | `/memory/{agent_id}` | Clear agent memory |

---

## 11. Example: End-to-End Flow

**Goal:** "Find the latest CoreWeave news, summarise it, and email me."

```
1. Supervisor receives goal
   → Logs: goal_received

2. Supervisor loads memory
   → Semantic: user prefers "modern minimalistic HTML" emails
   → Episodic: last run found 15 articles, email sent successfully

3. Supervisor plans
   → Subgoal 1: Research CoreWeave news (research_subagent)
   → Subgoal 2: Summarise findings (supervisor, AI step)
   → Subgoal 3: Email summary (notify_subagent)
   → Logs: plan_created

4. Approval gate checks plan
   → Subgoal 1: safe (news_skill GET) → auto-approved
   → Subgoal 2: safe (AI only) → auto-approved
   → Subgoal 3: irreversible (notification_skill POST /send) → PENDING
   → Logs: approval_requested

5. Subagent 1 (research) spawns and executes
   → Plans: news_skill/search + rss_new_skill/run
   → Executes steps, stores results in scratchpad
   → Publishes findings to supervisor scratchpad
   → Logs: subagent_spawned, step_started, step_completed

6. Supervisor compacts research output (if > 20K tokens)
   → AI summarises intermediate findings
   → Logs: memory_compacted

7. Supervisor runs AI summarisation step
   → Uses compacted research + semantic memory (email format preference)
   → Produces formatted summary

8. Human approves email step (via UI or API)
   → Logs: approval_granted

9. Subagent 3 (notify) spawns and executes
   → Sends email via notification_skill
   → Logs: step_completed

10. Supervisor aggregates, stores episode, updates semantic memory
    → Episode: "CoreWeave news: 12 articles, summary emailed"
    → Semantic: no new facts extracted
    → Logs: goal_completed
```

---

## 12. Security Considerations

1. **Skill scoping** — subagents only access skills listed in their config.
   The executor validates skill access before dispatching.

2. **Budget enforcement** — max steps, max HTTP calls, max wall-clock time
   per subagent and per goal. Exceeded budgets halt execution.

3. **Approval TTL** — pending approvals expire after `ttl_seconds` (default
   3600). Expired approvals auto-deny to prevent stale autonomous actions.

4. **Input validation** — all inter-agent messages pass through Pydantic
   models. No raw string passing between agents.

5. **Audit trail** — every action is logged to JSONL with goal_id and
   subagent_id for full traceability. Logs are append-only.

6. **Memory isolation** — each agent's memory is namespaced in storage.
   Subagents inherit read access to the supervisor's semantic memory but
   cannot write to it directly.
