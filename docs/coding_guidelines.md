# Coding Guidelines

These are mandatory standards for all code in this project. Every contributor and every AI agent must follow them without exception.

---

## 1. Monitor Decoration

All methods and functions **must** be decorated with `@monitor`.

The `@monitor` decorator (defined in `decorations/monitor.py`) logs start/finish timing, duration, and records exceptions with full stack traces to `./logs/monitor.log`.

### Function-level usage

```python
from decorations.monitor import monitor

@monitor
def calculate_total(items: list[float]) -> float:
    return sum(items)
```

### Class-level usage

Applying `@monitor` to a class wraps every method automatically:

```python
from decorations.monitor import monitor

@monitor
class OrderService:
    def create(self, data: dict) -> dict:
        ...

    def cancel(self, order_id: str) -> bool:
        ...
```

### FastAPI route monitoring

For FastAPI applications, call `monitor_fastapi_app(app)` after all routes are registered:

```python
from decorations.monitor import monitor_fastapi_app

app = FastAPI()
# ... register routes ...
monitor_fastapi_app(app)
```

**No exceptions.** If a function exists, it gets `@monitor`.

---

## 2. No Silent Failures

There must **never** be a silent failure anywhere in the codebase. Every `except` block must log or print the exception, no matter what.

### Prohibited patterns

```python
# FORBIDDEN — swallows the error silently
except Exception:
    pass

# FORBIDDEN — returns a fallback with no visibility
except Exception:
    return []

# FORBIDDEN — continues with no trace of what went wrong
except Exception:
    continue
```

### Required pattern

Every exception handler must make the error visible:

```python
except Exception as exc:
    print(f"[module_name] operation failed: {exc}", flush=True)
    return []  # fallback is fine, as long as the error is printed
```

### Rules

- Always capture the exception variable (`as exc` or `as e`).
- Always `print()` or log the exception before any `pass`, `continue`, `return`, or fallback logic.
- Use a `[module_name]` prefix so the source is immediately identifiable in logs.
- If the exception is re-raised, logging before the raise is still required.
- This applies to **all** exception types: `Exception`, `ValueError`, `KeyError`, `OSError`, `json.JSONDecodeError`, etc.

---

## 3. No `return None` — Use Response Structures

`return None` is **highly discouraged**. Functions should return a structured response that indicates success or failure, carries the result payload, and includes a failure code when applicable.

### Discouraged

```python
def find_user(user_id: str) -> dict | None:
    user = db.get(user_id)
    if not user:
        return None  # caller has no idea why — not found? DB error? permissions?
    return user
```

### Required pattern

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class Result:
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None

def find_user(user_id: str) -> Result:
    user = db.get(user_id)
    if not user:
        return Result(success=False, error="user not found", error_code="NOT_FOUND")
    return Result(success=True, data=user)
```

### Guidelines

- Return a structure with at minimum: `success` (bool), `data` (the payload), and `error` / `error_code` for failures.
- Pydantic `BaseModel`, `dataclass`, `TypedDict`, or a plain `dict` with consistent keys are all acceptable — pick one per module and be consistent.
- The caller should never have to guess whether `None` means "not found", "error", or "not applicable".
- For functions that currently return `None` on failure, migrate them to this pattern as you touch the code.

---

## 4. All Exceptions Must Be Logged

Whether an exception is handled, re-raised, or used for control flow, it **must** be logged. This is a superset of rule 2 — even exceptions that are "expected" get logged.

```python
# Control-flow exception — still log it
try:
    value = float(raw)
except ValueError as exc:
    print(f"[parser] non-numeric input {raw!r}: {exc}", flush=True)
    continue

# Re-raised exception — log before raising
try:
    response = client.get(url)
except httpx.HTTPError as exc:
    print(f"[client] request to {url} failed: {exc}", flush=True)
    raise

# Handled with fallback — log the reason for the fallback
try:
    data = response.json()
except json.JSONDecodeError as exc:
    print(f"[client] JSON decode failed, using raw text: {exc}", flush=True)
    data = response.text
```

### Why

- Silent failures are the single largest source of debugging time waste in this project.
- If something goes wrong, the logs must show exactly what happened, where, and why.
- "I didn't think this exception would ever fire" is not an acceptable reason to skip logging.

---

## 5. Storage Must Be Abstracted and File-Based

All persistent storage **must** use JSON or YAML files as the backing format. Direct use of raw file I/O in business logic is prohibited — all storage access must go through an abstraction layer (class or module) so that the underlying mechanism can be swapped to a database later without changing callers.

### Required pattern

```python
from abc import ABC, abstractmethod
from typing import Any

class RecordStore(ABC):
    @abstractmethod
    def get(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def put(self, key: str, value: dict[str, Any]) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...


class JsonFileStore(RecordStore):
    """Current implementation — JSON files on disk."""
    ...


class PostgresStore(RecordStore):
    """Future implementation — same interface, different backend."""
    ...
```

### Prefer the Storage Server

Wherever possible, use the **storage server** (`servers/storage`) rather than implementing local file I/O. The storage server provides a CRUD REST API and handles encryption automatically.

All data written to and read from the storage server is **encrypted at rest** using the `STORAGE_ENCRYPTION_KEY` from `.env`. The encryption is handled transparently by `servers/storage/encryption.py` — callers send and receive plain JSON, and the storage server encrypts before writing and decrypts after reading.

```
.env:
STORAGE_ENCRYPTION_KEY=your-fernet-key-here
```

- `PUT /namespaces/{ns}/records/{key}` — stores the JSON payload encrypted on disk.
- `GET /namespaces/{ns}/records/{key}` — decrypts and returns the plain JSON.
- `DELETE /namespaces/{ns}/records/{key}` — removes the encrypted record.

If you must implement local storage (e.g., for a skill that runs offline), use the abstract storage pattern above and back it with JSON or YAML files.

---

## 6. Transport Encryption

All HTTP request and response payloads between servers **must** be encrypted in transit using the `TRANSPORT_ENCRYPTION_KEY` from `.env`.

The encryption module lives at `common/transport_encryption.py` and provides `TransportEncryption` with `encrypt_json()` / `decrypt_json()` methods.

```
.env:
TRANSPORT_ENCRYPTION_KEY=your-fernet-key-here
```

### Usage

```python
from common.transport_encryption import get_transport_encryption

te = get_transport_encryption()

# Sending a request — encrypt the payload
encrypted_body = te.encrypt_json({"prompt": "hello", "profile": "default"})

# Receiving a request — decrypt the payload
original = te.decrypt_json(encrypted_body)
```

### Rules

- Every inter-server HTTP call must encrypt the request body before sending.
- Every server must decrypt the incoming request body before processing.
- Every server must encrypt the response body before returning.
- The caller must decrypt the response body after receiving.
- The `TRANSPORT_ENCRYPTION_KEY` must be set in `.env`. The server will raise `RuntimeError` at startup if it is missing.
- This is **separate** from storage encryption — transport encryption protects data in flight, storage encryption protects data at rest.

---

## 7. Server Lifecycle Scripts

Whenever a server is **created** or **removed** from the project, both `mgmt/start_app.py` and `mgmt/kill_start_app.py` **must** be updated to reflect the change.

### `mgmt/start_app.py`

The supervisor manages startup, port allocation, health checks, and registry registration for all servers. When adding a new server:

1. Add a new `ServerConfig` entry to the server list with the module path, default port, and health URL.
2. Ensure the server is registered with the registry after passing health checks.

When removing a server:

1. Remove its `ServerConfig` entry.
2. Remove any special-case logic that references it.

### `mgmt/kill_start_app.py`

The kill script uses pattern matching to find and terminate all project-related processes. When adding a new server:

1. Add its uvicorn module pattern (e.g., `"servers.myserver.main:app"`) to the `_PATTERNS` list.

When removing a server:

1. Remove its pattern from `_PATTERNS`.

### Why

If these scripts are not kept in sync, you will end up with:

- New servers that don't start with the rest of the application.
- Orphaned processes that survive `mgmt/kill_start_app.py` and cause port conflicts, stale responses, or phantom behavior on the next run.
- Hours of debugging "why is the old server still responding on port X."

---

## Summary Checklist

| Rule | Requirement |
|------|-------------|
| `@monitor` | Every function and method must be decorated |
| No silent failures | Every `except` block must print/log the exception |
| No `return None` | Use structured `Result` responses with success/failure/error_code |
| Log all exceptions | Handled, re-raised, or control-flow — all get logged |
| Abstracted storage | JSON/YAML backing with abstract classes; prefer the storage server |
| Storage encryption | All storage server data encrypted at rest via `STORAGE_ENCRYPTION_KEY` |
| Transport encryption | All inter-server HTTP payloads encrypted via `TRANSPORT_ENCRYPTION_KEY` |
| Server lifecycle scripts | `mgmt/start_app.py` and `mgmt/kill_start_app.py` must be updated when servers are added/removed |
