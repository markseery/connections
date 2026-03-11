"""
License: MIT
Description: Skill loading and mounting for the worker server.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.routing import APIRouter


@dataclass
class LoadedSkill:
    name: str
    module: str
    prefix: str


class SkillManager:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._loaded: dict[str, LoadedSkill] = {}

    def list_loaded(self) -> list[LoadedSkill]:
        return list(self._loaded.values())

    def is_loaded(self, skill_name: str) -> bool:
        return skill_name in self._loaded

    def load(self, skill_name: str) -> LoadedSkill:
        if self.is_loaded(skill_name):
            return self._loaded[skill_name]

        module_name = f"skills.{skill_name}"
        mod = importlib.import_module(module_name)

        get_router: Callable[[], APIRouter] | None = getattr(mod, "get_router", None)
        router_obj: APIRouter | None = None
        if callable(get_router):
            router_obj = get_router()
        else:
            router_obj = getattr(mod, "router", None)

        if not isinstance(router_obj, APIRouter):
            raise ValueError(f"Skill '{skill_name}' must export get_router() or router (APIRouter)")

        prefix = f"/skills/{skill_name}"
        self._app.include_router(router_obj, prefix=prefix, tags=[f"skill:{skill_name}"])

        loaded = LoadedSkill(name=skill_name, module=module_name, prefix=prefix)
        self._loaded[skill_name] = loaded
        return loaded

    def load_from_config(self, payload: dict[str, Any]) -> LoadedSkill:
        skill_name = str(payload.get("skill_name") or "").strip()
        if not skill_name:
            raise ValueError("skill_name is required")
        return self.load(skill_name)

