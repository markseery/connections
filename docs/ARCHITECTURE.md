## Connections architecture

This project is a small, local-first “agentic framework” built from multiple cooperating FastAPI servers started and supervised by `mgmt/start_app.py`.

### Goals
- **Composable services**: each concern is a small server with a health endpoint.
- **Service discovery**: servers publish their URL/port via the registry; clients look up by name.
- **Config-driven skills**: the agent discovers skills via the configuration server; skills run inside worker servers.
- **Optional transport encryption**: some inter-service calls support encrypted bodies (`_enc`) + `X-Transport-Encrypted: 1`.
- **Observable by default**: all server endpoints are wrapped by the `monitor` decorator to log latency and exceptions to `logs/monitor.log`.

---

## Runtime topology

### Supervisor (`mgmt/start_app.py`)
- Reads `app_config.yaml`
- Allocates unique ports (default 7000–7999 range)
- Starts each server via `uvicorn` in a new process group
- Performs periodic `/health` checks and restarts unhealthy servers
- Registers healthy servers with the registry (`PUT /servers/{name}`) including `host`, `port`, and `pid`
- Injects cross-server environment variables into child processes (notably `REGISTRY_SERVER_URL`, and `STORAGE_SERVER_URL` for configuration server)

### Registry server (`servers/registry`)
- Purpose: a simple registry of server name → `{ host, port, url, pid, createdAt, updatedAt }`
- Routes:
  - `GET /servers` list registrations
  - `GET /servers/{name}` lookup registration
  - `PUT /servers/{name}` register/update
  - `DELETE /servers/{name}` remove
- Persistence: `data/registry/registry.json`

### Storage server (`servers/storage`)
- Purpose: namespaced JSON records (CRUD + list keys) with **encryption at rest**
- Record identity: `(namespace, key)` where `key` is unique within a namespace
- Encryption: uses `STORAGE_ENCRYPTION_KEY` to encrypt stored JSON payloads
- List route returns keys (not the full objects)

### Configuration server (`servers/configuration`)
- Purpose: configuration records stored in the storage server under namespace `system`
- Record key format: `{resource_type}:{resource_name}`
- Uses transport encryption when talking to the storage server
- Used for:
  - server configuration values
  - **skill definitions** for agent discovery (resource_type=`skill`)

### AI server (`servers/aiserver`)
- Purpose: provider/profile-based generation behind a single `POST /generate`
- Inputs: `prompt`, `profile`, optional `provider`
- Provider/model mapping is driven by environment variables (see `docs/SETUP.md`)
- CLIs and skills that call aiserver should follow **`docs/SCRIPTS_AND_CLIENT_PATTERNS.md`** (use **`get_aiserver_base_url()`** from **`common.compound.aiserver_discovery`**).

### Connections UI (`servers/connections_ui`)
- Purpose: serve simple HTML/CSS/JS pages (e.g. chat)
- The browser discovers the chat server via `GET /api/chat-url` (registry lookup)

### Agent server (`servers/agent`)
- Purpose: execute a user request by:
  - discovering skills from configuration
  - generating a structured plan via AI server
  - calling skills over HTTP and composing results
- Main routes:
  - `POST /agent/execute` plan + execute
  - `POST /agent/plan` plan only
  - `GET /agent/jobs/{request_id}` job status
- Planning:
  - uses AI server profile `reason`
  - logs the raw AI request/response when `AGENT_TRACE_LLM=1`
- Execution:
  - executes steps in dependency order
  - resolves `$step.<id>.<path>` references from earlier step outputs
  - retries only on `500/503/504` (and replans only on those retryable failures)

### Worker server (`servers/worker`)
- Purpose: dynamically load skill modules from `./skills` and expose them under `/skills/<skill_name>/...`
- Skills are **auto-loaded on first request** via middleware — no explicit load call needed
- Skill management routes (mostly for introspection):
  - `GET /worker/skills` (list loaded skills)
  - `GET /worker/skills/{skill_name}/routes` (auto-loads if needed)
  - `POST /worker/skills/{skill_name}/load` (explicit load, rarely needed)
- Workers load `.env` at startup so skills can read configuration like SMTP credentials.

---

## Skill system

### Skill modules (`./skills`)
Skills are simple Python modules that export either:
- `router: fastapi.APIRouter`, or
- `get_router() -> APIRouter`

Workers mount them at:
- `/skills/<skill_name>` + (router’s internal paths)

Examples included:
- `statistics` (mean/median/stddev)
- `notification_skill` (SMTP email send, history)
- `stock_skill` (Yahoo Finance via `yfinance`)
- `webscraper_skill` (crawl; per-page storage + job markdown + optional summarize via AI server)

### Skill discovery (agent)
The agent discovers skills through the configuration server:
- list config keys: `GET /configs` → includes keys like `skill:statistics`
- fetch a skill record: `GET /configs/skill/<name>`
- expected value:
  - `base_url`: the worker base URL to call (e.g. `http://127.0.0.1:7030`)
  - `routes`: list of `{ method, path, description }`

This lets the agent plan against the skill’s known routes and then call them over HTTP at execution time.

---

## Encryption

### Transport encryption (optional)
`common/transport_encryption.py` provides helpers to encrypt/decrypt JSON via:
- request/response field `_enc` (string)
- `X-Transport-Encrypted: 1` header to request encrypted responses

Currently used primarily for:
- configuration ↔ storage calls

### At-rest encryption (storage)
Storage encrypts JSON records on disk using `STORAGE_ENCRYPTION_KEY`.

---

## Monitoring / observability

### `decorations/monitor.py`
- `@monitor` decorator wraps callables (sync or async) and logs:
  - start timestamp
  - finish timestamp + duration
  - exceptions with full stack traces
- Logs to `logs/monitor.log`

### Automatic endpoint monitoring
`monitor_fastapi_app(app)` wraps all FastAPI `APIRoute` endpoints at app startup.
All `servers/*/main.py` call this so every HTTP handler is monitored without manually decorating each route.

---

## Design principles
- **Small, explicit servers**: avoid “mega-app” modules; keep boundaries clear.
- **Discovery via registry**: don’t hardcode ports between processes.
- **Configuration via configuration server**: skills and other runtime mappings can be changed without code import wiring.
- **HTTP as the skill boundary**: agent never imports skill code; it calls skills by HTTP route.
- **Fail clearly**: validation errors are surfaced as 4xx; operational failures as 5xx; agent retries/replans only where it helps.
- **Core vs. user separation**: framework code is committed to git; user-specific content (`.env`, scripts, workflows, agent configs, custom skills, data, logs) lives in `application_files/` (gitignored; override via `CONNECTIONS_USER_DIR`). See `common/user_dir.py` and `docs/SETUP.md` for details.
