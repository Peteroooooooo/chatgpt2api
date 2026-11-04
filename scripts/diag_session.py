"""Read-only diagnosis: why does /api/auth/session return 403 for dropped accounts?

Run inside the chatgpt2api container: python /tmp/diag_session.py [limit]
Prints only status codes and a classification, never tokens.
"""
import json
import sys

sys.path.insert(0, "/app")

from curl_cffi.requests import Session  # noqa: E402

from services.openai_session_service import SESSION_URL, _session_cookies  # noqa: E402
from services.proxy_service import proxy_settings  # noqa: E402

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
accounts = json.load(open("/app/data/accounts.json"))
accounts = [a for a in accounts if a.get("session_token")][:limit]


def classify(text: str) -> str:
    low = text.lower()
    if "just a moment" in low or "cf-chl" in low or "challenge-platform" in low:
        return "cloudflare_challenge"
    if low.strip().startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            return "json?"
        if data.get("accessToken"):
            return "OK_access_token"
        return "json:" + ",".join(sorted(data.keys()))[:80]
    return "other:" + low[:60].replace("\n", " ")


for mode in ("proxy", "direct"):
    kwargs = proxy_settings.build_session_kwargs(impersonate="chrome", verify=True) if mode == "proxy" else {"impersonate": "chrome"}
    print(f"== {mode}: proxy={'yes' if kwargs.get('proxy') or kwargs.get('proxies') else 'no'}")
    for index, account in enumerate(accounts):
        with Session(**kwargs) as session:
            try:
                response = session.get(
                    SESSION_URL,
                    headers={"Cookie": _session_cookies(account["session_token"]), "Referer": "https://chatgpt.com/", "Accept": "application/json"},
                    timeout=30,
                )
                domain = str(account.get("email") or "").split("@")[-1]
                print(index, domain, response.status_code, classify(response.text or ""), "set-cookie" if response.headers.get("set-cookie") else "")
            except Exception as exc:
                print(index, "error", str(exc)[:120])
