# Creating and Using Skills

This guide explains how skills work in this project, how to create a new skill, and how to call skills from scripts and from the prompt-with-context runner.

---

## What is a skill?

A **skill** is a Python module in the `skills/` directory that the **worker server** can load on demand. Once loaded, the skill’s HTTP routes are mounted at:

- **Base path:** `{worker_url}/skills/{skill_name}/...`

For example, the `rss_skill` module exposes `/skills/rss_skill/feed` (GET and POST). The worker does not start with any skills loaded; callers must **load** a skill first, then call its endpoints. After a worker restart, all skills are unloaded again (404 until load is called).

---

## Worker API (skill lifecycle)

The worker exposes these routes under the `/worker` prefix:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/worker/skills` | List currently loaded skills |
| GET | `/worker/skills/{skill_name}/routes` | List routes for a loaded skill (404 if not loaded) |
| POST | `/worker/skills/{skill_name}/load` | Load a skill by name (idempotent if already loaded) |
| POST | `/worker/skills/load` | Load a skill from JSON body `{"skill_name": "..."}` |

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

The worker **discovers** skills by importing `skills.<skill_name>`. As long as the module lives under `skills/` and exports a router, it can be loaded via `POST /worker/skills/<skill_name>/load`.

---

## Calling a skill from a script

Typical pattern:

1. **Resolve the worker URL**  
   From the registry (e.g. `find_live_worker(registry_url)`) or from a CLI flag like `--worker-url`.

2. **Load the skill** (required after worker restart)  
   `POST {worker_url}/worker/skills/{skill_name}/load`  
   No body required for `load` by name. Raise for status or check response.

3. **Call the skill endpoint**  
   - **POST** (most common): `POST {worker_url}/skills/{skill_name}/{endpoint}` with `json=payload`.  
   - **GET**: `GET {worker_url}/skills/{skill_name}/{endpoint}?query=params` or path params.

Example (conceptually):

```python
import httpx
from common.skill_lifecycle import find_live_worker

worker_url = find_live_worker("http://127.0.0.1:7002").rstrip("/")

# Load so the route exists
httpx.post(f"{worker_url}/worker/skills/rss_skill/load").raise_for_status()

# Call the skill
r = httpx.post(f"{worker_url}/skills/rss_skill/feed", json={"url": "https://example.com/feed.xml"})
r.raise_for_status()
data = r.json()
```

If you skip the load step and the skill was not loaded (e.g. after a restart), the request to the skill endpoint will return **404**.

---

## Using skills in the prompt-with-context runner

The `run_prompt_with_context.py` runner can run a **skill step** as part of a multi-step config:

1. In the YAML config, add a step with `type: skill`, and specify `skill`, `endpoint`, and `params`.
2. The runner will:
   - Resolve the worker URL (registry).
   - Load the skill (`POST /worker/skills/{skill_name}/load`).
   - Call **POST** `{worker_url}/skills/{skill_name}/{endpoint}` with **JSON body** = `params` (after placeholder substitution).
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
| **stored_webscrape_skill** | Crawl a site, store URLs + content in storage; retrieve by base URL; parse combined_text | `website_marketing_analysis.py`, `StoredSiteContent` (common), `site_pages_ai.py`, `site_summarize.yaml` |
| **webscraper_skill** | Scrape and summarize (markdown, AI summary) | help_skill docs, chatagent |
| **help_skill** | List skills, examples, about | Chat/agent UIs |
| **stock_skill** | Quote, fundamentals, earnings | help_skill, agent |
| **news_skill** | Search news, topic/stock | help_skill, agent |
| **statistics** | mean, median, stddev | help_skill |
| **workflow_skill** | Templates, run/execute workflows | help_skill, agent |
| **agent_skill** | Send a prompt to the AI server and return the response | `scripts/run_agent.py`, prompt-with-context skill steps |

Scripts that call skills:

- **scripts/warmup_rss_list.py** – loads `rss_new_and_save_skill`, calls `run` with `list_name`, `warmup=True`.
- **scripts/site_pages_ai.py** – uses `StoredSiteContent`, which loads `stored_webscrape_skill` and calls `POST /stored` with `base_url` (and optional `max_chars`).
- **rss_notify_new.py** – loads `rss_new_and_save_skill` and `notification_skill`, calls run then send.
- **website_marketing_analysis.py** – loads `stored_webscrape_skill`, calls `/scrape`, polls `/scrape/{job_id}`, then GET `/stored?base_url=...`.
- **process_rss_feeds.py** – loads `rss_skill`, POST `/feed` with `{"url": ...}`.
- **scripts/run_agent.py** – loads `agent_skill`, POST `/respond` with `{"prompt": "...", "profile": "agent"}`.
- **application/run_prompt_with_context.py** – for each skill step: load skill by name, then POST to `/{endpoint}` with `params` as JSON body; optional `output_path` to take a subset of the response.

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

- **Skills** = FastAPI routers in `skills/`, mounted at `/skills/<skill_name>/...` after load.
- **Load** = `POST /worker/skills/<skill_name>/load` (required after worker restart).
- **Call** = HTTP GET or POST to `{worker_url}/skills/{skill_name}/{path}`; scripts often POST with JSON.
- **Runner** = skill steps in YAML use POST only; endpoint must accept JSON body; placeholders can be used in `params`.
- **Discovery** = worker imports `skills.<skill_name>`; no separate registration file needed.
