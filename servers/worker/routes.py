"""
License: MIT
Description: Worker routes: load skills and inspect loaded skills.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .skill_manager import SkillManager


router = APIRouter(prefix="/worker", tags=["worker"])


def _mgr(request: Request) -> SkillManager:
    mgr: SkillManager | None = getattr(request.app.state, "skill_manager", None)
    if mgr is None:
        mgr = SkillManager(request.app)
        request.app.state.skill_manager = mgr
    return mgr


@router.get("/skills")
def list_loaded_skills(request: Request) -> dict[str, Any]:
    loaded = _mgr(request).list_loaded()
    return {
        "loaded": [
            {"skill_name": s.name, "module": s.module, "prefix": s.prefix}
            for s in loaded
        ]
    }


@router.post("/skills/load")
def load_skill(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    loaded = _mgr(request).load_from_config(body)
    return {"status": "loaded", "skill_name": loaded.name, "prefix": loaded.prefix}


@router.post("/skills/{skill_name}/load")
def load_skill_by_name(request: Request, skill_name: str) -> dict[str, Any]:
    loaded = _mgr(request).load(skill_name.strip())
    return {"status": "loaded", "skill_name": loaded.name, "prefix": loaded.prefix}

