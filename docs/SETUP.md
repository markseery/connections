## Installation & setup

### Prerequisites
- Python 3.11+ (3.12 recommended)
- A local Ollama install (optional, for `AISERVER_DEFAULT_PROVIDER=ollama`)
- If using Google/OpenAI/xAI providers: valid API keys

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Initialize your user directory

```bash
python connections_init.py
```

This creates `application_files/` with subdirectories for your personal
config, scripts, workflows, notes, data, and logs.  It also copies
`.env.example` as a starting `.env` inside the user directory and sets up
the git pre-commit hook.  The `application_files/` directory is gitignored.

Override the location with `CONNECTIONS_USER_DIR`:
```bash
export CONNECTIONS_USER_DIR=/path/to/my/workspace
python connections_init.py
```

### Configure `.env`

Your `.env` file lives at `application_files/.env` (gitignored).  Edit it
to set your API keys and encryption keys:

```bash
$EDITOR application_files/.env
```

### Run the stack (supervised)

```bash
python start_app.py
```

This starts servers listed in `app_config.yaml`, monitors `/health`, and registers each server into the registry.

---

## User directory (`application_files/`)

The framework separates **core/shared code** (committed to git) from
**user-specific content** (gitignored in `application_files/`).

### Structure

```
application_files/
├── .env                  # Secrets and API keys
├── app_config.yaml       # Optional overrides (deep-merged with repo default)
├── config/
│   ├── skills/           # Skill config overrides (deep-merged)
│   └── agents/           # Agent configs (deep-merged)
├── skills/               # Custom user skills (auto-discovered by worker)
├── scripts/              # Personal scripts and utilities
├── workflows/            # Workflow YAML definitions
├── notes/                # Research notes, reports, etc.
├── data/                 # Runtime data (storage, reports, taxonomy)
│   └── reports/          # Workflow step reports
└── logs/                 # All log files (monitor, http, agent)
```

### Resolution order

All config loaders in `common/` follow the same pattern:

1. Check `application_files/<path>` first
2. Fall back to the repo default at `<repo>/<path>`
3. When both exist, the user file is **deep-merged** on top of the repo
   file — so you only need to specify overrides, not full copies.

### Custom skills

Place `.py` files in `application_files/skills/`.  The worker server discovers
them automatically alongside the built-in repo skills.  User skills follow
the same contract: export `router` or `get_router()`.

### Workflows

Place YAML files in `application_files/workflows/`.  The workflow executor
checks the user directory first when resolving config paths.

---

## Environment variables

At runtime, **`load_connections_dotenv()`** (in `common/simple/user_dir.py`) loads **both** `/.env` files when they exist: first **`<repo>/.env`**, then **`application_files/.env`** (user entries override the same variable name). Put secrets in either file; if you use both, keys only in the repo file still apply after the user file is loaded.

`resolve_env_file()` still returns a **single** path for tools that need one file (user `.env` if present, else repo). That is not the same as “which keys are loaded”—servers use the dual-file merge above.

Some values are required; many are optional.

### Core / required for most runs
- **`STORAGE_ENCRYPTION_KEY`**: Fernet key for encrypting records at rest in the storage server.
  - Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- **`TRANSPORT_ENCRYPTION_KEY`**: Fernet key used for optional encrypted HTTP bodies (`_enc`) between services.
  - Generate: same as above.

### Service discovery / wiring
- **`REGISTRY_SERVER_URL`**: base URL of the registry (used by clients/servers to discover others).
  - Example: `http://127.0.0.1:7002`
- **`STORAGE_SERVER_URL`**: base URL of the storage server (used by configuration server).
  - This is injected by `start_app.py` when it starts the configuration server, but it can also be set manually.

### AI server configuration (`servers/aiserver`)

#### Provider selection
- **`AISERVER_DEFAULT_PROVIDER`**: default provider when a profile has no per-profile provider.
  - Allowed: `ollama`, `openai`, `xai`, `google`
- **`AISERVER_PROFILE_<PROFILE>_PROVIDER`**: override provider for a specific profile.
  - Profiles: `FAST`, `CHAT`, `REASON`, `AGENT`, `CODE`, `IMAGE`, `VIDEO`
  - Example: `AISERVER_PROFILE_FAST_PROVIDER=google`

#### Model selection
- **`AISERVER_PROFILE_<PROFILE>_MODEL`**: override model for a specific profile.
  - Example: `AISERVER_PROFILE_FAST_MODEL=gemini-3-flash-preview`
- **`AISERVER_MODEL_<PROVIDER>_<PROFILE>`**: override model for a provider+profile pair.
  - Example: `AISERVER_MODEL_OLLAMA_REASON=gemma3:4b`

#### Provider API keys
- **`OPENAI_API_KEY`**: required if using provider `openai`
- **`XAI_API_KEY`**: required if using provider `xai`
- **`GOOGLE_API_KEY`**: required if using provider `google`

#### Optional provider base URLs
- **`OLLAMA_BASE_URL`**: default `http://localhost:11434`
- **`OPENAI_BASE_URL`**: default `https://api.openai.com`
- **`XAI_BASE_URL`**: default `https://api.x.ai`
- **`GOOGLE_BASE_URL`**: default `https://generativelanguage.googleapis.com`

### Agent tracing (optional)
- **`AGENT_TRACE`**: `1` (default) prints agent stage logs to stdout; set `0` to disable.
- **`AGENT_TRACE_LLM`**: `1` (default) prints the AI `/generate` request payload and raw response text; set `0` to disable.

### Skill: notification/email (`skills/notification_skill.py`)
- **`EMAIL_SENDER`**: SMTP username/from address.
- **`EMAIL_PASSWORD`**: SMTP password (for Gmail, typically an app password).
- **`EMAIL_SENDER_NAME`**: display name used in the From header.
- **`EMAIL_RECEIVER_DEFAULT`**: optional default recipient; used to replace placeholder emails.
- **`SMTP_HOST`**: SMTP host, default `smtp.gmail.com`.
- **`SMTP_PORT`**: SMTP port, default `587`.
- **`SMTP_USE_TLS`**: `true`/`false`, default `true` (STARTTLS).
- **`NOTIFICATION_THROTTLE_PER_MINUTE`**: per-recipient throttling window size, default `60`.

### Generic server port override (optional)
Some `run.py` entrypoints honor:
- **`PORT`**: the port uvicorn will bind to when running that server directly.

---

## Troubleshooting

### “Email not configured”
Ensure the worker process sees your `.env`. The worker loads `.env` on startup; if you run workers some other way, ensure `.env` is present or the variables are exported.

### Ports already in use
`start_app.py` will automatically pick a different free port in the configured range and will register the chosen port in the registry.

