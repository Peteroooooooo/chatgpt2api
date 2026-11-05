from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.support import require_admin
from services.model_rules import FEATURE_DEFAULTS
from services.model_service import model_catalog_service
from services.protocol import openai_v1_models


class ModelRulesUpdateRequest(BaseModel):
    models: dict[str, dict[str, Any]] | None = None
    features: dict[str, Any] | None = None
    default_policy: str | None = None


def _catalog(force: bool = False) -> dict[str, Any]:
    view = model_catalog_service.catalog_view(force=force)
    features = model_catalog_service.rules.features()
    known = [item["id"] for item in view["models"]]
    try:
        public_ids = [
            str(item.get("id"))
            for item in openai_v1_models.list_models().get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
    except Exception:  # noqa: BLE001 - the options list is a convenience
        public_ids = [item["id"] for item in view["models"] if item["result"] == "exposed"]
    view["features"] = features
    view["feature_defaults"] = dict(FEATURE_DEFAULTS)
    view["feature_options"] = sorted({*known, *FEATURE_DEFAULTS.values(), *features.values()})
    # What a key may be limited to: every model /v1/models offers, plus the search model.
    view["key_model_options"] = list(dict.fromkeys([*public_ids, features["search_model"]]))
    return view


def _apply_update(body: ModelRulesUpdateRequest) -> dict[str, Any]:
    rules = model_catalog_service.rules
    if body.default_policy is not None:
        rules.set_default_policy(body.default_policy)
    if body.models:
        shown = {item["id"]: item for item in model_catalog_service.catalog_view()["models"]}
        known = set(shown) | set(rules.all_rules())
        changes: dict[str, dict[str, Any]] = {}
        for model_id, change in body.models.items():
            if model_id not in known:
                raise ValueError(f"没有模型 {model_id}，先刷新模型列表")
            change = dict(change or {})
            if "account_types" in change and isinstance(change["account_types"], list):
                change["account_types"] = [
                    model_catalog_service.normalize_account_type(item) or str(item)
                    for item in change["account_types"]
                    if str(item or "").strip()
                ]
            changes[model_id] = change
        rules.update_rules(changes, defaults=shown)
    if body.features:
        rules.update_features(body.features)
    return _catalog()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/models/catalog")
    async def get_catalog(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(_catalog)

    @router.post("/api/models/refresh")
    async def refresh_catalog(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(_catalog, True)

    @router.put("/api/models/rules")
    async def update_rules(body: ModelRulesUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(_apply_update, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router
