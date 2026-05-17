# Scripts and client patterns

Single conventions for env loading, registry wiring, and calling the aiserver from scripts and skills.

---

## 1. Environment variables (`.env`)

### Loader

**`common.simple.user_dir.load_connections_dotenv()`** runs when server modules import (see `user_dir.py` docstring). It loads **`<repo>/.env`** then **`application_files/.env`** (user overrides on duplicate keys).

### `resolve_env_file()`

Returns one path for display or single-file tools. It does **not** define the full set of loaded keys.

### Standalone scripts

Repo-root CLIs import `common` directly. Anything under **`application_files/`** must put the repo root on **`sys.path`** *before* `from common...` (see `application_files/portfolio_analyser.py`).

---

## 2. Registry URL

Use **`get_registry_url()`** from **`common.compound.registry_client`** when you need the registry base URL.

Other modules may still read **`os.environ.get("REGISTRY_SERVER_URL", ...)`**; that matches the same default after dotenv load.

---

## 3. Aiserver base URL (only supported API)

**Always** use:

```python
from common.compound.aiserver_discovery import get_aiserver_base_url

base = get_aiserver_base_url()
```

Optional keyword arguments:

- **`explicit`** — e.g. CLI `--url`; skips the registry.
- **`registry_override`** — if it differs from **`get_registry_url()`**, updates **`REGISTRY_SERVER_URL`** and invalidates the cached aiserver URL (used by **`mgmt/ask_ai.py --registry-url`**).

On registry failure this returns **`AISERVER_DEV_FALLBACK`** (`http://127.0.0.1:7012`).

**`servers.agent.config.get_aiserver_url`** and **`servers.chatagent.config.get_aiserver_url`** delegate here — do not call **`get_server_url("aiserver")`** directly from new code.

For **other** services (storage, configuration, …), keep using **`get_server_url("<name>")`** from **`registry_client`**.

---

## 4. Calling the AI server (`POST /generate`)

- **URL:** `{get_aiserver_base_url()}/generate`
- **Body:** `{"prompt": str, "profile": str}`; optional `"provider"`.
- **Response:** JSON; `output` is usually `{"type": "text", "text": "..."}`.

**CLIs:** **`mgmt/ask_ai.py`**, **`application_files/portfolio_analyser.py`**.

**In-process:** `TestClient` on **`servers.aiserver.main:app`**, `POST /generate`.

Upstream provider errors (e.g. xAI JSON `error` / `code`) are surfaced as the HTTP **`detail`** string without duplicating the raw httpx message when JSON parsing succeeds.

---

## 5. Timeouts and errors

- Registry traffic uses the pooled client in **`registry_client.py`**.
- Long prompts may use a dedicated **`httpx.Client(timeout=...)`** on **`/generate`** (e.g. portfolio script **600s**).
- Missing provider API keys → **503** via **`MissingProviderApiKeyError`**.

---

## 6. Checklist for a new script

1. `sys.path` if under **`application_files/`**.
2. Import **`get_aiserver_base_url`**; do not reimplement registry GETs.
3. POST JSON to **`{base}/generate`**.

See **`docs/SETUP.md`** for env variable names.
