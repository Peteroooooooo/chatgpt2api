from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from api.support import require_admin
from services.daily_chat import daily_chat_service


def _overview() -> dict[str, Any]:
    settings = daily_chat_service.get_settings()
    today = daily_chat_service.load_plan(daily_chat_service.today())
    return {
        "settings": settings,
        "today": today,
        "counts": daily_chat_service.counts(today) if today else {},
        "next_item_at": daily_chat_service.next_item_at(today),
        "days": daily_chat_service.list_days(),
    }


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/daily-chat")
    async def get_overview(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return _overview()

    @router.post("/api/daily-chat/settings")
    async def save_settings(body: dict[str, Any], authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            daily_chat_service.update_settings(body or {})
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return _overview()

    @router.post("/api/daily-chat/reschedule")
    async def reschedule(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        daily_chat_service.reschedule_today()
        return _overview()

    @router.post("/api/daily-chat/items/{item_id}/run")
    async def run_item(item_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = daily_chat_service.run_now(item_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "item not found"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        return {"item": item}

    @router.get("/api/daily-chat/days/{day}")
    async def get_day(day: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            from datetime import date

            plan = daily_chat_service.load_plan(date.fromisoformat(day))
        except ValueError:
            plan = None
        if plan is None:
            raise HTTPException(status_code=404, detail={"error": "day not found"})
        return {**plan, "counts": daily_chat_service.counts(plan)}

    return router
