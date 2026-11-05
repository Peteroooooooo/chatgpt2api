from __future__ import annotations

from typing import Any

from services.account_service import account_service
from services.model_rules import model_rules_store
from services.openai_backend_api import OpenAIBackendAPI


def current_model() -> str:
    """The search model chosen on the model management page (default gpt-5-5)."""
    return model_rules_store.feature_model("search_model")


def search_with_account(prompt: str) -> tuple[dict[str, Any], str]:
    """Run one search on an account allowed to run the configured search model.

    Returns (result, access_token). Raises ModelUnavailableError when no account can run it.
    """
    from services.model_service import ModelUnavailableError, model_catalog_service

    model = current_model()
    token = account_service.get_text_access_token(model=model, internal=True)
    if not token:
        raise ModelUnavailableError(
            f"搜索模型 {model} 现在没有登录账号能跑，搜索需要登录账号",
            status_code=403,
            code="model_no_account",
        )
    backend = OpenAIBackendAPI(token)
    try:
        result = backend.search(prompt, model=model)
    except Exception as exc:
        model_catalog_service.record_conversation_result(token, model, exc)
        raise
    finally:
        backend.close()
    model_catalog_service.record_conversation_result(token, model)
    account_service.mark_text_used(token)
    return result, token


def handle(body: dict[str, object]) -> dict[str, object]:
    result, token = search_with_account(str(body["prompt"]))
    account = account_service.get_account(token) or {}
    result["_account_email"] = str(account.get("email") or "")
    return result
