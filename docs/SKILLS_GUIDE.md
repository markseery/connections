# Creating and Using Skills

This guide explains how skills work in this project, how to create a new skill, and how to call skills from scripts and from the prompt-with-context runner.

---

## What is a skill?

A **skill** is a Python module in the `skills/` directory that the **worker server** can load on demand. Once loaded, the skill’s HTTP routes are mounted at:

- **Base path:** `{worker_url}/skills/{skill_name}/...`

For example, the `rss_skill` module exposes `/skills/rss_skill/feed` (GET and POST). Skills are **auto-loaded on first request** — the worker middleware detects the skill name from the URL path and loads the module before routing the request. No explicit load call is needed.

---

## Worker API (skill lifecycle)

The worker exposes these routes under the `/worker` prefix:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/worker/skills` | List currently loaded skills |
| GET | `/worker/skills/{skill_name}/routes` | List routes for a skill (auto-loads if needed) |
| POST | `/worker/skills/{skill_name}/load` | Explicitly load a skill by name (idempotent; rarely needed since auto-load handles this) |
| POST | `/worker/skills/load` | Load a skill from JSON body `{"skill_name": "..."}` (rarely needed) |

**Skill name** is the module name under `skills/` (e.g. `rss_skill` for `skills/rss_skill.py`).

---

## Creating a new skill

### 1. Add a module under `skills/`

- **File:** `skills/<skill_name>.py` (e.g. `skills/my_skill.py`).
- **Skill name** used in URLs is the module name: `my_skill` → `/skills/my_skill/...`.

### 2. Export an APIRouter

The worker expects the module to expose a **FastAPI `APIRouter`** in one of two ways:

- **Option A:** Export a callable `get_router()` that returns an `APIRouter`:
  ```python
  router = APIRouter()

  @router.post("/run")
  def run(body: MyRequest) -> dict[str, Any]:
      ...

  def get_router() -> APIRouter:
      return router
  ```
- **Option B:** Export a module-level `router` (an `APIRouter` instance).

The worker mounts that router at prefix `/skills/{skill_name}`, so a route `@router.post("/run")` becomes:

- `POST {worker_url}/skills/my_skill/run`

### 3. Define routes and request/response shapes

- Use **FastAPI** decorators: `@router.get(...)`, `@router.post(...)`.
- Use **Pydantic** models for request bodies and validation (e.g. `body: MyRequest`).
- Query parameters and path parameters work as usual (e.g. `@router.get("/item/{id}")`).
- Return JSON-serializable values (dict, list); FastAPI will serialize them.

### 4. Document dependencies in the module docstring

Mention what the skill needs at runtime, for example:

- Registry URL (worker discovery, storage URL)
- Storage server (if the skill reads/writes namespaces)
- Aiserver (if the skill calls `/generate`)
- Other skills (if the skill calls another skill on the same worker)

Example:

```python
"""
Description: My skill does X.

Input: ... (body or query).
Requires: registry, storage (optional), worker (for other skills).
"""
```

### 5. No need to register the skill elsewhere

The worker **discovers** skills by importing `skills.<skill_name>`. As long as the module lives under `skills/` and exports a router, it will be **auto-loaded on first request** to any `/skills/<skill_name>/...` path.

---

## Calling a skill from a script

Typical pattern:

1. **Resolve the worker URL**  
   From the registry (e.g. `find_live_worker(registry_url)`) or from a CLI flag like `--worker-url`.

2. **Call the skill endpoint** — skills are auto-loaded on first request, so no explicit load step is needed.  
   - **POST** (most common): `POST {worker_url}/skills/{skill_name}/{endpoint}` with `json=payload`.  
   - **GET**: `GET {worker_url}/skills/{skill_name}/{endpoint}?query=params` or path params.

Example (conceptually):

```python
import httpx
from common.skill_lifecycle import find_live_worker

worker_url = find_live_worker("http://127.0.0.1:7002").rstrip("/")

# Just call the skill — it auto-loads on first request
r = httpx.post(f"{worker_url}/skills/rss_skill/feed", json={"url": "https://example.com/feed.xml"})
r.raise_for_status()
data = r.json()
```

---

## Using skills in the prompt-with-context runner

The `run_prompt_with_context.py` runner can run a **skill step** as part of a multi-step config:

1. In the YAML config, add a step with `type: skill`, and specify `skill`, `endpoint`, and `params`.
2. The runner will:
   - Resolve the worker URL (registry).
   - Call **POST** `{worker_url}/skills/{skill_name}/{endpoint}` with **JSON body** = `params` (after placeholder substitution). The skill auto-loads on first request.
   - Use the response (or a subset via `output_path`) as `previous_output` for the next step.

So for a skill to be usable from a **skill step** in the runner, the target route must accept **POST** with a **JSON body**. GET-only routes are fine for direct script use but cannot be used as a runner skill step unless you add a POST variant.

Example config snippet:

```yaml
steps:
  - id: fetch
    type: skill
    skill: rss_skill
    endpoint: feed
    params:
      url: "https://news.ycombinator.com/rss"
    # output_path: items   # optional; omit to pass full response as previous_output

  - id: summarize
    type: ai
    prompt: |
      Summarize:
      {previous_output}
    profile: agent
```

Placeholders like `{previous_output}`, `{step_1_output}`, and config `vars` are substituted into `params` and into the prompt of later steps.

---

## Existing skills and how they’re used

| Skill | Purpose | Example callers / usage |
|-------|--------|--------------------------|
| **rss_skill** | Fetch and parse one RSS/Atom feed | `process_rss_feeds.py`, `rss_new_skill` (internal), prompt configs |
| **rss_new_skill** | Fetch new items from a feed list, fetch article content, diff vs storage | `rss_new_and_save_skill`, warmup script |
| **rss_new_and_save_skill** | Run rss_new_skill and persist new item IDs to storage | `rss_notify_new.py`, warmup script, `cloud_services_notify_multistep.yaml` |
| **notification_skill** | Send email (SMTP), list/stats | `rss_notify_new.py`, `send_test_email.py`, notify configs |
| **webscraper_skill** | Single crawler: per-page storage (`webscrape` namespace), job markdown + optional summary, `/pages` CRUD, `/stored` aggregate for tools | `website_marketing_analysis.py`, `StoredSiteContent`, `webscrape_save.py`, `webscrape_site_facts.py`, help_skill, chatagent |
| **help_skill** | List skills, examples, about | Chat/agent UIs |
| **stock_skill** | Quote, fundamentals, earnings | help_skill, agent |
| **news_skill** | Search news, topic/stock | help_skill, agent |
| **statistics** | mean, median, stddev | help_skill |
| **workflow_skill** | Templates, run/execute workflows | help_skill, agent |
| **agent_skill** | Send a prompt to the AI server and return the response | `scripts/run_agent.py`, prompt-with-context skill steps |

Scripts that call skills (all auto-loaded on first request):

- **scripts/warmup_rss_list.py** – calls `rss_new_and_save_skill/run` with `list_name`, `warmup=True`.
- **scripts/webscrape_save.py** – exports stored scrape to `data/webscrape/sites/*.md` via worker `webscraper_skill`.
- **scripts/webscrape_site_facts.py** – reads markdown from `data/webscrape/sites/`, runs aiserver on batched pages, writes JSON facts to `data/webscrape/facts/`.
- **rss_notify_new.py** – calls `rss_new_and_save_skill/run` then `notification_skill/send`.
- **website_marketing_analysis.py** – calls `webscraper_skill/scrape`, polls `/scrape/{job_id}`, then GET `/stored?base_url=...`.
- **process_rss_feeds.py** – POST `rss_skill/feed` with `{"url": ...}`.
- **scripts/run_agent.py** – POST `agent_skill/respond` with `{"prompt": "...", "profile": "agent"}`.
- **application/run_prompt_with_context.py** – for each skill step: POST to `/{endpoint}` with `params` as JSON body; optional `output_path` to take a subset of the response.

---

## Skill response format (for chat/UI)

To avoid per-skill formatting in chat and UI servers, skills should return a **common shape** when possible. The shared formatter (`common.skill_response.skill_response_to_markdown`) renders:

| Field | Use |
|-------|-----|
| **summary** | Human-readable summary (markdown allowed). Optional **query** is shown above it when present. |
| **items** or **articles** | List of display items. Each: `title`, `link` or `url`, optional `summary`. |
| **text** | Plain or markdown body. |

Skills that return this shape get consistent markdown (headings, lists, links) in agent_chat and chatagent. Any other keys are left for APIs; the formatter falls back to a generic key-value or JSON view. No skill-specific branches in the chat server.

---

## Checklist for a new skill

- [ ] Add `skills/<skill_name>.py` (name = module name in URLs).
- [ ] Create an `APIRouter()` and define routes (GET/POST).
- [ ] Export it via `def get_router() -> APIRouter: return router` or a top-level `router`.
- [ ] Use Pydantic models for POST bodies where useful.
- [ ] Document in the module docstring: description, input, and required services (registry, storage, aiserver, other skills).
- [ ] If the skill should be callable from **run_prompt_with_context** skill steps, ensure the target endpoint accepts **POST** with a **JSON body** (or add a POST variant).
- [ ] Optionally add the skill and its routes to `help_skill.py` so it appears in the help list and examples.
- [ ] Prefer the standard response shape (**summary**, **items** or **articles**, **text**) for consistent chat/UI display without server customizations.

---

## Summary

- **Skills** = FastAPI routers in `skills/`, auto-loaded and mounted at `/skills/<skill_name>/...` on first request.
- **Auto-load** = Middleware intercepts `/skills/{name}/...` requests and loads the skill module if not yet mounted. No explicit load call needed.
- **Call** = HTTP GET or POST to `{worker_url}/skills/{skill_name}/{path}`; scripts often POST with JSON.
- **Runner** = skill steps in YAML use POST only; endpoint must accept JSON body; placeholders can be used in `params`.
- **Discovery** = worker imports `skills.<skill_name>`; no separate registration file needed.
