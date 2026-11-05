import re
import unittest

from curl_cffi.requests.impersonate import BrowserType

from utils import browser_profile


class BrowserProfileTests(unittest.TestCase):
    def test_impersonate_target_exists_in_curl_cffi(self) -> None:
        self.assertIn(browser_profile.IMPERSONATE, {item.value for item in BrowserType})

    def test_headers_name_the_impersonated_chrome_version(self) -> None:
        major = re.fullmatch(r"chrome(\d+)", browser_profile.IMPERSONATE).group(1)
        self.assertIn(f"Chrome/{major}.0.0.0", browser_profile.USER_AGENT)
        self.assertIn(f'"Google Chrome";v="{major}"', browser_profile.SEC_CH_UA)
        self.assertIn(f'"Chromium";v="{major}"', browser_profile.SEC_CH_UA)
        self.assertTrue(browser_profile.CHROME_FULL_VERSION.startswith(f"{major}."))
        self.assertNotIn("Edg/", browser_profile.USER_AGENT)

    def test_sentinel_defaults_use_the_profile(self) -> None:
        from utils import sentinel

        self.assertEqual(sentinel.DEFAULT_SENTINEL_USER_AGENT, browser_profile.USER_AGENT)
        self.assertEqual(sentinel.DEFAULT_SENTINEL_SEC_CH_UA, browser_profile.SEC_CH_UA)


if __name__ == "__main__":
    unittest.main()
