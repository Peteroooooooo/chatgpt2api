"""The one browser every request to OpenAI / chatgpt.com pretends to be.

curl_cffi's TLS + HTTP/2 fingerprint (IMPERSONATE) and the headers below
must name the same Chrome release; change them together.
The sec-ch-ua brand list is the one curl_cffi's chrome116 target sends.

Chrome 116 rather than a newer target: from the production proxy (WARP),
Cloudflare on chatgpt.com challenges curl_cffi's chrome119+ fingerprints
(chrome145 got 403 cf-mitigated on every request) while chrome110/116 pass.
Re-test with a real request through the prod proxy before moving it.
"""
from __future__ import annotations

CHROME_MAJOR = "116"
CHROME_FULL_VERSION = "116.0.5845.188"  # a Windows stable build of Chrome 116

IMPERSONATE = f"chrome{CHROME_MAJOR}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36"
)
SEC_CH_UA = f'"Chromium";v="{CHROME_MAJOR}", "Not)A;Brand";v="24", "Google Chrome";v="{CHROME_MAJOR}"'
SEC_CH_UA_FULL_VERSION = f'"{CHROME_FULL_VERSION}"'
SEC_CH_UA_FULL_VERSION_LIST = (
    f'"Chromium";v="{CHROME_FULL_VERSION}", "Not)A;Brand";v="24.0.0.0", "Google Chrome";v="{CHROME_FULL_VERSION}"'
)
SEC_CH_UA_MOBILE = "?0"
SEC_CH_UA_PLATFORM = '"Windows"'


def base_headers() -> dict[str, str]:
    """Session-level headers, so requests that set only some of them never
    fall back to curl_cffi's built-in defaults (which claim macOS)."""
    return {
        "user-agent": USER_AGENT,
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
    }
