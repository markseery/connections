"""
License: MIT
Description: Reusable skill catalog that discovers available skills, fetches their
OpenAPI parameter schemas from the worker, and renders a human-readable catalog
suitable for embedding in AI prompts.

Usage:
    from common.skill_catalog import SkillCatalog

    catalog = SkillCatalog(
        registry_url="http://127.0.0.1:7002",
        blocked_routes={"POST /skills/webscraper_skill/scrape"},
    )
    catalog.discover()

    # Plain-text catalog for prompt injection
    text = catalog.render()

    # Structured access
    for route in catalog.routes:
        print(route.method, route.path, route.schema_text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.http_client import http_client
from common.skill_lifecycle import find_live_worker
from servers.agent.skill_discovery import SkillDefinition, SkillRoute, discover_skills

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


@dataclass
class CatalogRoute:
    """A single route entry with optional parameter schema text."""

    method: str
    path: str
    description: str = ""
    schema_text: str = ""

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


class SkillCatalog:
    """Discovers skills, resolves their parameter schemas from OpenAPI, and
    renders a prompt-ready catalog.

    The class separates *discovery* (``discover()``) from *rendering*
    (``render()``) so callers can inspect the structured ``routes`` list
    before producing the final text.
    """

    def __init__(
        self,
        registry_url: str = "http://127.0.0.1:7002",
        worker_url: str | None = None,
        blocked_routes: set[str] | None = None,
        local_files: list[str] | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self._worker_url = worker_url
        self.blocked_routes: set[str] = blocked_routes or set()
        self.local_files: list[str] = local_files or []

        self.skills: list[SkillDefinition] = []
        self.routes: list[CatalogRoute] = []
        self._openapi_schemas: dict[str, str] = {}

    @property
    def worker_url(self) -> str | None:
        return self._worker_url

    def discover(self) -> None:
        """Run full discovery: find worker, fetch skills, fetch OpenAPI schemas."""
        if not self._worker_url:
            self._worker_url = find_live_worker(self.registry_url)

        if self._worker_url:
            self._openapi_schemas = _fetch_openapi_schemas(self._worker_url)

        try:
            self.skills = discover_skills(registry_url=self.registry_url)
        except Exception as exc:
            print(f"[skill_catalog] skill discovery failed: {exc}", flush=True)
            self.skills = []

        self.routes = self._build_routes()

    @property
    def route_methods(self) -> dict[str, str]:
        """Map ``path -> HTTP method`` for all non-blocked routes."""
        return {r.path: r.method for r in self.routes}

    def _build_routes(self) -> list[CatalogRoute]:
        routes: list[CatalogRoute] = []
        for skill in self.skills:
            for r in skill.routes:
                key = f"{r.method} {r.path}"
                if key in self.blocked_routes:
                    continue
                routes.append(CatalogRoute(
                    method=r.method,
                    path=r.path,
                    description=r.description,
                    schema_text=self._openapi_schemas.get(key, ""),
                ))
        return routes

    def render(
        self,
        *,
        header: str = "AVAILABLE SKILLS (you may request any of these during the conversation):",
        request_instruction: str = (
            "To use a skill, put this on its own line:\n"
            '  [SKILL_REQUEST: /full/route/path {"param": "value", ...}]\n'
            "Use the exact route path and parameter names from the list above."
        ),
        file_instruction: str = (
            "To read a local file, put this on its own line:\n"
            "  [READ_FILE: path/to/file.md]"
        ),
    ) -> str:
        """Render the catalog as a human-readable string for prompt injection."""
        lines: list[str] = []

        for r in self.routes:
            line = f"- {r.key} — {r.description}"
            if r.schema_text:
                line += f"\n    {r.schema_text}"
            lines.append(line)

        if self.local_files:
            lines.append("")
            lines.append("LOCAL FILES (previously scraped website content you can read):")
            for f in self.local_files:
                lines.append(f"- {f}")
            if file_instruction:
                lines.append("")
                lines.append(file_instruction)

        if not lines:
            return ""

        return f"{header}\n" + "\n".join(lines) + f"\n\n{request_instruction}\n"


# ── OpenAPI schema extraction ─────────────────────────────────────────────


def _fetch_openapi_schemas(worker_url: str) -> dict[str, str]:
    """Fetch the worker's OpenAPI spec and return ``{"METHOD /path": "param description", ...}``."""
    schemas: dict[str, str] = {}
    try:
        with http_client("skill_catalog", timeout=10.0) as client:
            r = client.get(f"{worker_url.rstrip('/')}/openapi.json")
        if r.status_code != 200:
            return schemas
        spec = r.json()
    except Exception:
        return schemas

    components = spec.get("components", {}).get("schemas", {})

    def _resolve_ref(ref: str) -> dict[str, Any]:
        return components.get(ref.rsplit("/", 1)[-1], {})

    def _describe_schema(schema: dict[str, Any]) -> str:
        if "$ref" in schema:
            schema = _resolve_ref(schema["$ref"])
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        if not props:
            return ""
        parts: list[str] = []
        for name, prop in props.items():
            if "$ref" in prop:
                prop = _resolve_ref(prop["$ref"])
            ptype = prop.get("type", "")
            if "anyOf" in prop:
                types = [t.get("type", "") for t in prop["anyOf"] if isinstance(t, dict)]
                ptype = " | ".join(t for t in types if t)
            desc = prop.get("description", "")
            default = prop.get("default")
            req = "(required)" if name in required else ""
            if default is not None and not req:
                req = f"(default: {default})"
            enum = prop.get("enum")
            if enum:
                desc += f" Options: {enum}"
            items = prop.get("items")
            if ptype == "array" and items:
                item_type = items.get("type", "")
                if item_type:
                    ptype = f"array[{item_type}]"
            line = f"{name}: {ptype}"
            if req:
                line += f" {req}"
            if desc:
                line += f" — {desc}"
            parts.append(line)
        return "; ".join(parts)

    paths = spec.get("paths", {})
    for path, methods in paths.items():
        for method_lower, operation in methods.items():
            if method_lower not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            method = method_lower.upper()

            body_schema = (
                operation
                .get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            query_params = operation.get("parameters", [])
            param_parts: list[str] = []

            if body_schema:
                desc = _describe_schema(body_schema)
                if desc:
                    param_parts.append(f"Body: {{{desc}}}")

            if query_params:
                qparts: list[str] = []
                for p in query_params:
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name", "")
                    pschema = p.get("schema", {})
                    ptype = pschema.get("type", "")
                    preq = "(required)" if p.get("required") else ""
                    default = pschema.get("default")
                    if default is not None and not preq:
                        preq = f"(default: {default})"
                    pdesc = p.get("description", "")
                    q = f"{pname}: {ptype}"
                    if preq:
                        q += f" {preq}"
                    if pdesc:
                        q += f" — {pdesc}"
                    qparts.append(q)
                if qparts:
                    param_parts.append(f"Query: {'; '.join(qparts)}")

            if param_parts:
                schemas[f"{method} {path}"] = " | ".join(param_parts)

    return schemas
