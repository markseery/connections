# Design: Integrate workflow_skill strengths into the workflow server

Date: 2026-03-22

**Implementation status (2026-03-22):** Phases 1–2 and parts of 4 are in `WorkflowExecutor`: skill **`poll`**, **`$step.<id>.<path>`** resolution (params, routes, subprocess argv, AI prompt/files), **`depends_on`** topological ordering, **`continue_on_error`**, optional full-worker **`route`** + **`method`** on skill steps. Workflow server uses a bounded **`ThreadPoolExecutor`** (`WORKFLOW_MAX_WORKERS`, default 8) with graceful shutdown on app lifespan. JSON template conversion is out of scope.

## 1. Goal

**One orchestration system:** the **workflow server** + **`WorkflowExecutor`** (YAML configs) becomes the single place to define and run multi-step workflows.

**Absorb from `workflow_skill` (JSON templates):** async **polling**, **cross-step data wiring** (`$step` references), **dependency / error policy** primitives, and **parameter ergonomics**—without giving up YAML strengths: **`ai`** steps (files + aiserver), **`subprocess`** steps, and the existing **`/workflows/submit`** job API.

**Non-goals (initial phases):** replacing the worker skill mesh; changing how individual skills implement their HTTP APIs.

---

## 2. What each system does well today

| Capability | `workflow_skill` (JSON) | Workflow server (YAML) |
|------------|-------------------------|-------------------------|
| Call skills over HTTP | Native (`method`, `route`, `args`) | Yes (`type: skill`) |
| Long-running async jobs | **`poll`** until `field == target` | No (single POST, no poll loop) |
| Pass prior step output into next step | **`$step.<id>.<path>`** in routes/args | Limited (`step_responses` exists but not wired into string resolution for all fields) |
| Step ordering beyond linear | **`depends_on`** | Strictly linear list |
| Failure handling | **`continue_on_error`** | Fail-fast |
| Parameters | **`params`**, **`optional_params`**, **`param_aliases`**, **`param_constraints`** | **`vars`** + `{key}` substitution only |
| AI + files | Not built-in | **`type: ai`** |
| Local scripts | Not built-in | **`type: subprocess`** |
| Job progress API | Via skill execution | **`/workflows/jobs/{id}`** (richer step model possible) |

**Conclusion:** The workflow server should keep **YAML**, **ai**, and **subprocess**, and **extend `type: skill`** (and optionally a renamed **`type: http`**) with **poll**, **structured scratchpad**, and **`$step` resolution** aligned with `workflow_skill`.

---

## 3. Target architecture (high level)

```
Client
  │  POST /workflows/submit { config, vars, timeouts }
  ▼
Workflow server
  │  WorkflowExecutor.run(config_path, var_overrides)
  │    for each step (still sequential by default):
  │      - build placeholders: vars + previous_output + step_N_output + $step scratchpad
  │      - ai → aiserver /generate (unchanged)
  │      - subprocess → subprocess.run (unchanged)
  │      - skill → HTTP to worker; optional poll loop; store full JSON in scratchpad[step_id]
  ▼
Registry / aiserver / worker (unchanged)
```

**Scratchpad:** After each step, store a normalized record, e.g. `{ "raw": <last response body>, "parsed": <dict if JSON> }`, keyed by **`step_id`** (string). This mirrors `workflow_skill`’s `scratchpad[int]` but uses string ids consistent with YAML.

---

## 4. YAML schema extensions

### 4.1 `type: skill` — add optional `poll`

When present, after the initial request the executor **polls** until success or exhaustion (same semantics as `workflow_skill._poll_until_ready`).

```yaml
- id: scrape
  type: skill
  skill: webscraper_skill
  endpoint: scrape          # POST .../skills/webscraper_skill/scrape  (existing shape)
  params:
    url: "{url}"
    max_pages: "{max_pages}"
  poll:
    route: /skills/webscraper_skill/scrape/$step.scrape.job_id   # GET; $step refs resolved from scratchpad
    method: GET               # default GET
    field: status
    target: completed
    interval: 2
    max_attempts: 120
  # Optional: which response feeds "output" string for reports / previous_output
  output_from: poll_final     # default: poll_final | initial | path:data.job_id
  output_path: data.markdown  # if JSON, extract path like today
```

**Design choices:**

- **`poll.route`** is a path template (leading slash, relative to worker base URL), matching `workflow_skill` style.
- **Resolve `$step.<step_id>.<dotted.path>`** in `route`, `params`, and (if needed) `prompt`/`command` using the scratchpad **before** each HTTP call.
- **Final step output (string)** for `previous_output` / reports: default = JSON string of the **last successful poll response** (or initial POST response if no poll), optionally sliced by `output_path`.

### 4.2 `$step` reference syntax

Align with existing templates: **`$step.<step_id>.<json_path>`** where `step_id` matches YAML `id` (e.g. `scrape`, `extract`).

- If the reference is the **entire** string, preserve JSON types when inserting into `params` (numbers, bools) where the HTTP client accepts them—same idea as `_resolve_string_refs` in `workflow_skill`.
- If **embedded** in a larger string, coerce to string.

**Edge case:** Numeric legacy ids from old configs—support `$step.1.field` if `step_id` is `"1"`.

### 4.3 `depends_on` (optional phase)

For **DAG** execution:

```yaml
- id: summarize
  type: skill
  depends_on: [scrape]
```

**Executor change:** topological sort of steps; run only when dependencies have completed; if a dependency was skipped (`run_when` / `skip_if_previous_empty`), define policy: skip dependent vs fail.

**Recommendation:** Defer to **Phase 3**; many flows stay **linear** with `$step` only.

### 4.4 `continue_on_error`

```yaml
- id: optional_enrich
  type: skill
  continue_on_error: true
```

On failure: record error in scratchpad, set `previous_output` to empty or error message, **do not** abort workflow (unless a later step requires success—define explicitly).

### 4.5 Params ergonomics (optional)

Map JSON concepts into top-level workflow metadata (not only `vars`):

```yaml
params: [url, topic]                    # required param names
optional_params:
  max_pages: 30
param_aliases:
  pages: max_pages
param_constraints:
  max_pages: { min: 1, max: 200 }
```

**On submit:** merge `SubmitRequest.vars` with defaults; validate constraints; reject **400** with clear detail before starting the thread.

This removes ad-hoc “missing sitename” issues and matches `workflow_skill` UX.

---

## 5. Execution algorithm (skill + poll)

1. Resolve `params` and initial route with `{vars}` and `$step` refs.
2. `POST` (or `GET`/`PUT` if extended) to worker; capture response JSON.
3. Store in `scratchpad[step_id]`.
4. If **`poll`** absent: extract string output via existing `output_path` logic; return.
5. If **`poll` present:**
   - Resolve `poll.route` with `$step` (may reference this step’s initial response, e.g. `job_id`).
   - Loop: `sleep(interval)` → `GET` poll URL → read `field` → compare to `target`; on `failed` state, fail step; on match, store final body in scratchpad and return extracted output.
6. Respect **`skill_timeout`** / **`workflow_subprocess`**-style budget: **poll deadline** = min(per-step timeout, global skill timeout).

Reuse **`httpx`** via existing `http_client` patterns for consistency and timeouts.

---

## 6. Migration from JSON templates

1. **Mechanical mapping document** (one page): `step_id` → `id`, `route` → `endpoint` + `skill`, `args` → `params`, `poll` block copied with `$step` syntax checked.
2. **Converter script** (optional): `scripts/json_workflow_to_yaml.py` for `data/workflows/*.json` → `data/workflows/*.yaml` (lossless for skill-only steps; AI/subprocess added manually).
3. **Dual-run period:** keep `workflow_skill` read-only; new workflows only in YAML; deprecate JSON after N templates migrated.
4. **Chat / agent integration:** route “run workflow X” to **`POST /workflows/submit`** with `config: X.yaml` and `vars`, instead of `workflow_skill/run/{name}`—or add a thin **adapter skill** that only forwards to the workflow server (temporary).

---

## 7. API and persistence follow-ups

| Topic | Recommendation |
|--------|----------------|
| Job store | Persist job state (SQLite or storage server) so restarts don’t lose in-flight workflows. |
| Step progress | Extend `WorkflowStepProgress` with `poll_attempt`, `last_poll_field` for observability. |
| List configs | Already merges `CONFIG_DIR` + `data/workflows`; document single convention: **`data/workflows` for product workflows**. |
| Security | Subprocess remains gated: optional **allowlist** of command prefixes or env `WORKFLOW_SUBPROCESS_ALLOW=1`. |

---

## 8. Phased delivery

| Phase | Scope | Outcome |
|-------|--------|---------|
| **1** | Poll loop on `type: skill` + scratchpad JSON storage | Parity with `webscrape_and_extract`-style flows |
| **2** | `$step` resolution in `params`, `poll.route`, and subprocess `command` argv | End-to-end wiring without duplicating job ids manually |
| **3** | `depends_on` + topological run order | DAG workflows |
| **4** | `continue_on_error` + optional `params` / `param_constraints` | Resilience + validation |
| **5** | Deprecate `workflow_skill` execute path; optional adapter skill | Single user-facing workflow system |

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| YAML + `$step` typos | Validate references at load time against listed `id`s; fail fast with step list in error |
| Poll storms | Default max_attempts; backoff optional; same limits as `workflow_skill` config |
| Two ways to call skills (`skill` vs raw `http`) | Keep one canonical `type: skill`; document `endpoint` as path segment only |
| Subprocess + network skills in one graph | Clear timeout hierarchy documented in `SubmitRequest` |

---

## 10. Summary

Integrating **`workflow_skill`** into the **workflow server** means **enriching YAML `skill` steps** with **polling**, a **real scratchpad**, and **`$step` references**, then optionally adding **DAG** and **param validation**. The workflow server keeps **AI** and **subprocess**, giving you **one config format** and **one HTTP API** for operations and UI, while preserving the **async orchestration** patterns that JSON workflows already proved out.
