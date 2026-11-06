from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from services.account_service import account_service
from services.model_rules import model_rules_store
from services.openai_backend_api import OpenAIBackendAPI

# One search may try a second account when the first fails before any answer text.
SEARCH_ATTEMPTS = 2


def current_model() -> str:
    """The search model chosen on the model management page."""
    return model_rules_store.feature_model("search_model")


def search_model_for(requested: object = None) -> str:
    """The request's own model when the pool offers it publicly, else the configured search model.

    Requests name models such as gpt-6-mini or auto for a web_search tool call; names the
    pool does not serve (gpt-4o-search-preview, an empty model) use the configured one.
    """
    from services.model_service import model_catalog_service

    model = str(requested or "").strip()
    if model:
        try:
            route = model_catalog_service.route_for_model(model)
        except Exception:  # noqa: BLE001 - fall back to the configured model
            route = None
        if route is not None and route.known and route.exposed and route.account_types:
            return model
    return current_model()


def search_events(prompt: str, model: str | None = None) -> Iterator[dict[str, Any]]:
    """Search on an account allowed to run ``model``; yield answer deltas, then the result.

    Events: {"type": "delta", "text"} while the answer is written, then
    {"type": "done", "result", "token"}. Raises ModelUnavailableError when no account can run it.
    """
    from services.model_service import ModelUnavailableError, model_catalog_service

    model = model or current_model()
    tried: set[str] = set()
    last_error: Exception | None = None
    for _attempt in range(SEARCH_ATTEMPTS):
        try:
            token = account_service.get_text_access_token(excluded_tokens=tried, model=model, internal=True)
        except ModelUnavailableError:
            if last_error is not None:
                raise last_error from None
            raise
        if not token or token in tried:
            if last_error is not None:
                raise last_error
            raise ModelUnavailableError(
                f"搜索模型 {model} 现在没有登录账号能跑，搜索需要登录账号",
                status_code=403,
                code="model_no_account",
            )
        tried.add(token)
        backend = OpenAIBackendAPI(token)
        sent_text = False
        try:
            for event in backend.search_events(prompt, model=model):
                if event["type"] == "delta":
                    sent_text = True
                    yield event
                elif event["type"] == "done":
                    model_catalog_service.record_conversation_result(token, model)
                    account_service.mark_text_used(token)
                    yield {**event, "token": token}
            return
        except Exception as exc:
            model_catalog_service.record_conversation_result(token, model, exc)
            if sent_text:
                raise
            last_error = exc
        finally:
            backend.close()
    if last_error is not None:
        raise last_error


def search_with_account(prompt: str, model: str | None = None) -> tuple[dict[str, Any], str]:
    """Run one search to the end. Returns (result, access_token)."""
    result: dict[str, Any] = {}
    token = ""
    for event in search_events(prompt, model):
        if event["type"] == "done":
            result, token = event["result"], event["token"]
    if not token:
        raise RuntimeError("search returned no result")
    return result, token


def handle(body: dict[str, object]) -> dict[str, object]:
    result, token = search_with_account(str(body["prompt"]), search_model_for(body.get("model")))
    account = account_service.get_account(token) or {}
    result["_account_email"] = str(account.get("email") or "")
    return result
