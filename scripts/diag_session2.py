"""Read-only: retry /api/auth/session through the WARP runtime proxy with FlareSolverr clearance.

python /tmp/diag_session2.py [limit]. Prints status + classification only.
"""
import json
import sys

sys.path.insert(0, "/app")

from curl_cffi.requests import Session  # noqa: E402

from services.openai_session_service import SESSION_URL, _session_cookies  # noqa: E402
from services.proxy_service import proxy_settings  # noqa: E402

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 4
accounts = [a for a in json.load(open("/app/data/accounts.json")) if a.get("session_token")][:limit]


def classify(response) -> str:
    text = response.text or ""
    low = text.lower()
    if "just a moment" in low or "cf-chl" in low or "challenge-platform" in low:
        return "cloudflare_challenge"
    try:
        data = json.loads(text)
    except Exception:
        return "other:" + low[:60].replace("\n", " ")
    if data.get("accessToken"):
        return "OK_access_token expires=" + str(data.get("expires"))
    return "json:" + ",".join(sorted(data.keys()))[:80]


try:
    bundle = proxy_settings.refresh_clearance(target_url="https://chatgpt.com", upstream=True)
    print("clearance refreshed:", bool(bundle))
except TypeError:
    bundle = proxy_settings.refresh_clearance("https://chatgpt.com")
    print("clearance refreshed (positional):", bool(bundle))
except Exception as exc:
    print("clearance error:", str(exc)[:200])

kwargs = proxy_settings.build_session_kwargs(impersonate="chrome", verify=True, upstream=True)
print("proxy:", kwargs.get("proxy"))
for index, account in enumerate(accounts):
    headers = proxy_settings.build_headers(
        {"Cookie": _session_cookies(account["session_token"]), "Referer": "https://chatgpt.com/", "Accept": "application/json"},
        target_url=SESSION_URL,
        upstream=True,
    )
    with Session(**kwargs) as session:
        try:
            response = session.get(SESSION_URL, headers=headers, timeout=40)
            print(index, str(account.get("email") or "").split("@")[-1], response.status_code, classify(response))
        except Exception as exc:
            print(index, "error", str(exc)[:160])
