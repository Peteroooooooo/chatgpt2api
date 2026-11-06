from __future__ import annotations

import base64
import json
import time
import unittest
from unittest import mock

from services import openai_session_service
from services.openai_session_service import SessionRefreshError, refresh_chatgpt_session


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class _Response:
    status_code = 200
    ok = True
    text = ""
    cookies: dict[str, str] = {}

    def __init__(self, access_token: str) -> None:
        self._payload = {"accessToken": access_token, "expires": "2027-02-04T00:00:00.000Z"}

    def json(self) -> dict:
        return self._payload


class SessionRefreshTests(unittest.TestCase):
    def _refresh(self, access_token: str) -> dict:
        session = mock.MagicMock()
        session.get.return_value = _Response(access_token)
        with mock.patch.object(openai_session_service, "Session", return_value=session), \
                mock.patch.object(openai_session_service.proxy_settings, "build_session_kwargs", return_value={}), \
                mock.patch.object(openai_session_service.proxy_settings, "build_headers", side_effect=lambda headers, **_: headers):
            return refresh_chatgpt_session({"session_token": "cookie"})

    def test_fresh_token_is_returned(self) -> None:
        token = _jwt(time.time() + 10 * 86400)

        self.assertEqual(self._refresh(token)["access_token"], token)

    def test_expired_token_from_the_session_cookie_is_a_failure(self) -> None:
        with self.assertRaises(SessionRefreshError):
            self._refresh(_jwt(time.time() - 3600))


if __name__ == "__main__":
    unittest.main()
