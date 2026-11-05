from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from api.support import require_admin
from services.account_keeper import account_keeper
from services.mailbox.service import mailbox_service


class MailboxSettingsRequest(BaseModel):
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    otp_timeout_seconds: int | None = None
    poll_interval_seconds: int | None = None


class MailboxResolveRequest(BaseModel):
    email: str = ""


class PasswordImportRequest(BaseModel):
    text: str = ""


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/keeper/mailboxes")
    async def get_mailboxes(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return mailbox_service.public_settings()

    @router.post("/api/keeper/mailboxes")
    async def save_mailboxes(body: MailboxSettingsRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return mailbox_service.update_settings(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/keeper/mailboxes/{name}/test")
    async def test_mailbox(name: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        from fastapi.concurrency import run_in_threadpool

        try:
            return await run_in_threadpool(mailbox_service.test, name)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/api/keeper/mailboxes/resolve")
    async def resolve_mailbox(body: MailboxResolveRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            provider = mailbox_service.resolve(body.email)
        except Exception as exc:
            return {"email": body.email, "mailbox": None, "error": str(exc)}
        return {"email": body.email, "mailbox": provider.name if provider else None, "kind": provider.kind if provider else None}

    @router.get("/api/keeper/settings")
    async def get_keeper_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {**account_keeper.get_settings(), "next_run_at": account_keeper.next_run_at().isoformat()}

    @router.post("/api/keeper/settings")
    async def save_keeper_settings(body: dict[str, Any], authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = account_keeper.update_settings(body or {})
        return {**settings, "next_run_at": account_keeper.next_run_at().isoformat()}

    @router.post("/api/keeper/patrol/run")
    async def run_patrol(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return account_keeper.start_patrol("manual")

    @router.get("/api/keeper/patrol/runs")
    async def list_patrol_runs(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": account_keeper.list_runs()}

    @router.get("/api/keeper/patrol/runs/{run_id}")
    async def get_patrol_run(run_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        run = account_keeper.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail={"error": "run not found"})
        return run

    @router.post("/api/keeper/import-passwords")
    async def import_passwords(body: PasswordImportRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        job = account_keeper.start_import(body.text)
        if not job["total"]:
            raise HTTPException(status_code=400, detail={"error": "没有识别到邮箱（每行一个邮箱，或 邮箱----密码）"})
        return job

    @router.get("/api/keeper/jobs/{job_id}")
    async def get_job(job_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        job = account_keeper.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "job not found"})
        return job

    return router
