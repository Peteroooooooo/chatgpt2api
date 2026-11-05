"""The one browser every request to OpenAI / chatgpt.com pretends to be.

curl_cffi's TLS + HTTP/2 fingerprint (IMPERSONATE) and the headers below
must name the same Chrome release; change them together.
The sec-ch-ua brand list is the one curl_cffi's chrome145 target sends.
"""
from __future__ import annotations

CHROME_MAJOR = "145"
CHROME_FULL_VERSION = "145.0.7632.68"  # a Windows stable build of Chrome 145

IMPERSONATE = f"chrome{CHROME_MAJOR}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36"
)
SEC_CH_UA = f'"Not:A-Brand";v="99", "Google Chrome";v="{CHROME_MAJOR}", "Chromium";v="{CHROME_MAJOR}"'
SEC_CH_UA_FULL_VERSION = f'"{CHROME_FULL_VERSION}"'
SEC_CH_UA_FULL_VERSION_LIST = (
    f'"Not:A-Brand";v="99.0.0.0", "Google Chrome";v="{CHROME_FULL_VERSION}", "Chromium";v="{CHROME_FULL_VERSION}"'
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
