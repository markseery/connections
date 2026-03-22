"""
License: MIT
Description: Worker routes: load skills and inspect loaded skills.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute

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


@router.get("/skills/{skill_name}/routes")
def skill_routes(request: Request, skill_name: str) -> dict[str, Any]:
    """Return route metadata for a loaded skill (auto-loads if needed)."""
    name = skill_name.strip()
    mgr = _mgr(request)
    if not mgr.is_loaded(name):
        try:
            mgr.load(name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load skill '{name}': {exc}") from exc
    prefix = f"/skills/{name}"
    routes: list[dict[str, str]] = []
    for route in request.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(prefix):
            continue
        for method in route.methods or []:
            routes.append({
                "method": method.upper(),
                "path": route.path,
                "description": route.summary or route.name or "",
            })
    return {"skill_name": name, "routes": routes}


@router.post("/skills/load")
def load_skill(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        loaded = _mgr(request).load_from_config(body)
        return {"status": "loaded", "skill_name": loaded.name, "prefix": loaded.prefix}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load skill: {exc!s}. Check that dependencies are installed (e.g. pip install -r requirements.txt).",
        )


@router.post("/skills/{skill_name}/load")
def load_skill_by_name(request: Request, skill_name: str) -> dict[str, Any]:
    try:
        loaded = _mgr(request).load(skill_name.strip())
        return {"status": "loaded", "skill_name": loaded.name, "prefix": loaded.prefix}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load skill '{skill_name}': {exc!s}. Check that dependencies are installed (e.g. pip install -r requirements.txt).",
        )

