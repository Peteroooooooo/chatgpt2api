from __future__ import annotations

import json
import math
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from services.config import DATA_DIR
from services.signup import generate_strong_password
from utils.pkce import generate_pkce

_BROWSER_LOCK = Lock()

_FIRST_NAMES = (
    "Daniel", "Lucas", "Nathan", "Hannah", "Claire",
    "Ethan", "Sophia", "Owen", "Grace", "Caleb",
    "Mason", "Olivia", "Liam", "Chloe", "Noah",
)
_LAST_NAMES = (
    "Miller", "Parker", "Bennett", "Hayes", "Brooks",
    "Foster", "Reed", "Ward", "Cole", "Turner",
    "Grant", "Hayes", "Morgan", "Scott", "Price",
)

_OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
_OAUTH_REDIRECT_URI = "https://platform.openai.com/auth/callback"
_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
_OAUTH_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"


def is_browser_signup_available() -> bool:
    try:
        import patchright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _pick_identity() -> tuple[str, int, int, int]:
    first = random.choice(_FIRST_NAMES)
    last = random.choice(_LAST_NAMES)
    year = random.randint(1982, 2002)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{first} {last}", year, month, day


def _playwright_proxy(proxy_url: str) -> dict[str, str] | None:
    raw = str(proxy_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    cfg: dict[str, str] = {"server": server}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


@contextmanager
def _virtual_display():
    forced_headless = os.getenv("CHATGPT2API_BROWSER_HEADLESS", "").strip().lower()
    if forced_headless in {"1", "true", "yes"}:
        yield True
        return
    if sys.platform != "linux" or os.environ.get("DISPLAY"):
        yield False
        return
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        yield True
        return
    display_num = random.randint(90, 190)
    display = f":{display_num}"
    proc = subprocess.Popen(
        [xvfb, display, "-screen", "0", "1366x860x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    time.sleep(0.5)
    try:
        yield False
    finally:
        if old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old_display
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


class _HumanMouse:
    def __init__(self, page):
        self.page = page
        self.x = random.uniform(200, 500)
        self.y = random.uniform(200, 400)

    def move_to(self, tx: float, ty: float) -> None:
        sx, sy = self.x, self.y
        dist = math.hypot(tx - sx, ty - sy)
        steps = max(10, min(28, int(dist / 25)))
        cx1 = sx + (tx - sx) * random.uniform(0.25, 0.45) + random.uniform(-40, 40)
        cy1 = sy + (ty - sy) * random.uniform(0.25, 0.45) + random.uniform(-40, 40)
        cx2 = sx + (tx - sx) * random.uniform(0.55, 0.75) + random.uniform(-30, 30)
        cy2 = sy + (ty - sy) * random.uniform(0.55, 0.75) + random.uniform(-30, 30)
        for i in range(1, steps + 1):
            t = i / steps
            u = 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
            mt = 1 - u
            nx = mt**3 * sx + 3 * mt**2 * u * cx1 + 3 * mt * u**2 * cx2 + u**3 * tx + random.uniform(-1.2, 1.2)
            ny = mt**3 * sy + 3 * mt**2 * u * cy1 + 3 * mt * u**2 * cy2 + u**3 * ty + random.uniform(-1.2, 1.2)
            self.page.mouse.move(nx, ny)
            time.sleep(random.uniform(0.008, 0.022))
        self.x, self.y = tx, ty

    def click_locator(self, locator, *, timeout: float = 10000, optional: bool = False) -> bool:
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
            time.sleep(random.uniform(0.12, 0.28))
            box = locator.bounding_box(timeout=timeout)
        except Exception:
            if optional:
                return False
            raise
        if not box:
            if optional:
                try:
                    locator.click(timeout=timeout)
                    return True
                except Exception:
                    return False
            locator.click(timeout=timeout)
            return True
        tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        self.move_to(tx, ty)
        time.sleep(random.uniform(0.08, 0.22))
        self.page.mouse.down()
        time.sleep(random.uniform(0.04, 0.11))
        self.page.mouse.up()
        return True

    def type_locator(self, locator, text: str, *, clear_first: bool = False) -> None:
        self.click_locator(locator)
        time.sleep(random.uniform(0.15, 0.35))
        if clear_first:
            self.page.keyboard.press("Control+a")
            time.sleep(random.uniform(0.05, 0.12))
            self.page.keyboard.press("Backspace")
            time.sleep(random.uniform(0.08, 0.18))
        for ch in text:
            self.page.keyboard.type(ch, delay=random.uniform(45, 145))
            if random.random() < 0.08:
                time.sleep(random.uniform(0.12, 0.28))


def _find_continue_btn(page):
    exact = page.get_by_role("button", name="Continue", exact=True)
    if exact.count() > 0 and exact.first.is_visible():
        return exact.first
    return page.locator(
        'button[type="submit"]:not(:has-text("Google")):not(:has-text("Apple")):not(:has-text("Microsoft")):not(:has-text("phone"))'
    ).first


def _find_one_time_code_btn(page):
    return page.locator(
        'button:has-text("one-time code"), a:has-text("one-time code"), '
        'button:has-text("One-time code"), a:has-text("One-time code"), '
        'button:has-text("email code"), a:has-text("email code"), '
        'button:has-text("Email code"), a:has-text("Email code")'
    ).first


def _is_login_password_page(page) -> bool:
    url = (page.url or "").lower()
    if "log-in/password" in url or "login/password" in url:
        return True
    if "create-account" in url:
        return False
    if _find_one_time_code_btn(page).count() > 0:
        return True
    try:
        txt = page.locator("h1, h2, [role='heading']").first.inner_text(timeout=800).lower()
        if "enter your password" in txt:
            return True
    except Exception:
        pass
    return False


def _find_onboarding_btn(page):
    for label in ("Continue", "Okay, let's go", "Get started", "Skip"):
        btn = page.get_by_role("button", name=label, exact=True)
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
    return None


def _detect_fatal_auth_error(page) -> tuple[str, str] | None:
    try:
        body = page.locator("body").inner_text(timeout=400)
    except Exception:
        return None
    low = body.lower()
    if "account_deactivated" in low or "deleted or deactivated" in low:
        return "account_deactivated", body[:300]
    if "unsupported_country" in low or "not available in your country" in low:
        return "unsupported_country_region_territory", body[:300]
    if "too many" in low or "rate limit" in low:
        return "rate_limit_exceeded", body[:300]
    return None


def _save_error_shot(page) -> str:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "browser_signup_last_error.png"
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return ""


def _log(msg: str) -> None:
    print(f"[browser-signup] {msg}", file=sys.stderr, flush=True)


def _is_chatgpt_app_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return host in {"chatgpt.com", "www.chatgpt.com"} and not path.startswith("/auth")


def _is_cloudflare_challenge(page) -> bool:
    try:
        title = (page.title() or "").lower()
        if "just a moment" in title or "attention required" in title:
            return True
    except Exception:
        pass
    for frame in page.frames:
        furl = (frame.url or "").lower()
        if "challenges.cloudflare.com" in furl:
            return True
    return False


def _find_turnstile_checkbox_point(page) -> tuple[float, float, str] | None:
    for frame in page.frames:
        furl = (frame.url or "").lower()
        if "challenges.cloudflare.com" not in furl and "turnstile" not in furl:
            continue
        try:
            fbox = frame.frame_element().bounding_box(timeout=1500)
        except Exception:
            fbox = None
        if fbox and fbox["width"] > 0 and fbox["height"] > 0:
            return (
                fbox["x"] + random.uniform(22, 34),
                fbox["y"] + fbox["height"] * random.uniform(0.42, 0.58),
                "frame",
            )

    try:
        rect = page.evaluate("""() => {
            const walk = (root) => {
                for (const el of root.querySelectorAll('*')) {
                    const src = (el.getAttribute && (el.getAttribute('src') || '')) || '';
                    const id = el.id || '';
                    const cls = typeof el.className === 'string' ? el.className : '';
                    if (
                        (el.tagName === 'IFRAME' && (src.includes('challenges.cloudflare.com') || src.includes('turnstile'))) ||
                        id.includes('turnstile') ||
                        cls.includes('cf-turnstile')
                    ) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            return {x: r.x, y: r.y, w: r.width, h: r.height, src: 'dom'};
                        }
                    }
                    if (el.shadowRoot) {
                        const hit = walk(el.shadowRoot);
                        if (hit) return hit;
                    }
                }
                return null;
            };
            const hit = walk(document);
            if (hit) return hit;

            const resp = document.querySelector('input[name="cf-turnstile-response"]');
            if (resp && resp.parentElement) {
                const pr = resp.parentElement.getBoundingClientRect();
                if (pr.width >= 200 && pr.height >= 45) {
                    return {x: pr.left, y: pr.top, w: pr.width, h: pr.height, src: 'cf_input_parent'};
                }
            }

            // Closed Shadow DOM fallback: innermost text node above the Turnstile widget
            const needles = ['This website uses a security service', 'verifies you are not a bot'];
            for (const el of document.querySelectorAll('p, div, span, h1, h2')) {
                const txt = (el.textContent || '').trim();
                if (!needles.some(n => txt.includes(n))) continue;
                const childHasIt = Array.from(el.children || []).some(c => {
                    const ct = (c.textContent || '').trim();
                    return needles.some(n => ct.includes(n));
                });
                if (childHasIt) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 100 && r.height > 0 && r.height < 160 && r.bottom > 0) {
                    return {x: r.left, y: r.bottom + 24, w: 300, h: 65, src: 'inferred_leaf'};
                }
            }
            return null;
        }""")
        if rect and rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
            return (
                float(rect["x"]) + random.uniform(22, 34),
                float(rect["y"]) + float(rect["h"]) * random.uniform(0.42, 0.58),
                str(rect.get("src") or "dom"),
            )
    except Exception:
        pass
    return None


def _click_turnstile_if_ready(page, mouse: _HumanMouse, last_click: float, *, min_interval: float = 6.5) -> float:
    if time.time() - last_click < min_interval:
        return last_click
    point = _find_turnstile_checkbox_point(page)
    if not point:
        return last_click
    tx, ty, source = point
    _log(f"clicking Cloudflare checkbox ({source}) at ({tx:.0f}, {ty:.0f}) on {page.url[:80]}")
    mouse.move_to(tx, ty)
    time.sleep(random.uniform(0.12, 0.28))
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.12))
    page.mouse.up()
    return time.time()


def _wait_until_ready_with_cf(page, mouse: _HumanMouse, ready_check, *, timeout_seconds: float = 48.0) -> tuple[str, str, str]:
    deadline = time.time() + timeout_seconds
    last_cf_click = 0.0
    while time.time() < deadline:
        fatal = _detect_fatal_auth_error(page)
        if fatal:
            return "fatal", fatal[0], fatal[1]
        try:
            if ready_check():
                return "ready", "", ""
        except Exception:
            pass
        if _is_cloudflare_challenge(page) or _find_turnstile_checkbox_point(page) is not None:
            last_cf_click = _click_turnstile_if_ready(page, mouse, last_cf_click)
        time.sleep(1.5)
    fatal = _detect_fatal_auth_error(page)
    if fatal:
        return "fatal", fatal[0], fatal[1]
    return "timeout", "", ""


def _wait_for_auth_landing(page, mouse: _HumanMouse) -> bool:
    ready_selector = (
        'button:has-text("Sign up"), a:has-text("Sign up"), '
        '[data-testid="signup-button"], input[type="email"], input[name="email"]'
    )
    status, _, _ = _wait_until_ready_with_cf(
        page,
        mouse,
        lambda: page.locator(ready_selector).count() > 0,
        timeout_seconds=45.0,
    )
    if status == "ready":
        _log(f"landing ready: {page.url[:90]}")
        return True
    return False


def _launch_browser_context(playwright, profile_dir: str, *, headless: bool, proxy: dict[str, str] | None):
    kwargs: dict[str, Any] = {
        "user_data_dir": profile_dir,
        "headless": headless,
        "viewport": {"width": 1366, "height": 860},
        "locale": "en-US",
    }
    if sys.platform == "linux":
        kwargs["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
    else:
        kwargs["ignore_default_args"] = ["--no-sandbox"]
    if proxy:
        kwargs["proxy"] = proxy
    try:
        return playwright.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        return playwright.chromium.launch_persistent_context(**kwargs)


def register_via_browser(
    email: str,
    password: str,
    mailbox,
    *,
    proxy_url: str = "",
) -> dict[str, Any]:
    """Register (or sign in) via real anti-detect Chrome and capture Platform OAuth tokens in the same session."""
    if not is_browser_signup_available():
        return {"ok": False, "error": "browser_unavailable", "detail": {"reason": "patchright is not installed"}}

    from patchright.sync_api import sync_playwright

    full_name, year, month, day = _pick_identity()
    has_known_password = bool(password and password.strip())
    chosen_password = password.strip() if has_known_password else generate_strong_password()
    proxy_cfg = _playwright_proxy(proxy_url)

    with _BROWSER_LOCK:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile_dir:
            with _virtual_display() as headless:
                with sync_playwright() as p:
                    ctx = _launch_browser_context(p, profile_dir, headless=headless, proxy=proxy_cfg)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    mouse = _HumanMouse(page)
                    try:
                        return _execute_browser_flow(
                            ctx=ctx,
                            page=page,
                            mouse=mouse,
                            email=email,
                            password=chosen_password,
                            has_known_password=has_known_password,
                            full_name=full_name,
                            year=year,
                            month=month,
                            day=day,
                            mailbox=mailbox,
                        )
                    except Exception as exc:
                        shot = _save_error_shot(page)
                        return {
                            "ok": False,
                            "error": "browser_signup_failed",
                            "detail": {"reason": str(exc), "url": page.url[:200], "screenshot": shot},
                        }
                    finally:
                        try:
                            ctx.close()
                        except Exception:
                            pass


def _execute_browser_flow(
    *,
    ctx,
    page,
    mouse: _HumanMouse,
    email: str,
    password: str,
    has_known_password: bool,
    full_name: str,
    year: int,
    month: int,
    day: int,
    mailbox,
) -> dict[str, Any]:
    _log(f"start flow for {email}")
    page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=45000)
    if not _wait_for_auth_landing(page, mouse):
        return {
            "ok": False,
            "error": "signup_csrf_failed_403",
            "detail": {"reason": "Cloudflare challenge did not clear", "url": page.url[:200], "screenshot": _save_error_shot(page)},
        }

    signup_btn = page.locator('button:has-text("Sign up"), a:has-text("Sign up"), [data-testid="signup-button"]').first
    if signup_btn.count() > 0 and signup_btn.is_visible():
        mouse.click_locator(signup_btn)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(random.uniform(1.8, 3.0))

    email_input = page.locator('input[type="email"], input[name="email"], input[id="email-input"], input[autocomplete="email"]').first
    status, err_code, err_body = _wait_until_ready_with_cf(
        page,
        mouse,
        lambda: email_input.count() > 0 and email_input.is_visible(),
        timeout_seconds=45.0,
    )
    if status == "fatal":
        return {"ok": False, "error": err_code, "detail": {"url": page.url[:200], "text": err_body, "screenshot": _save_error_shot(page)}}
    if status != "ready":
        return {"ok": False, "error": "signup_csrf_failed_403", "detail": {"reason": "email input not reached (Cloudflare?)", "url": page.url[:200], "screenshot": _save_error_shot(page)}}

    _log(f"typing email {email}")
    mouse.type_locator(email_input, email)
    time.sleep(random.uniform(0.4, 0.8))
    started_at = datetime.now(timezone.utc)
    mouse.click_locator(_find_continue_btn(page))
    time.sleep(random.uniform(1.8, 3.0))

    pwd_input = page.locator('input[type="password"], input[name="password"], input[name="new-password"]').first
    otp_input = page.locator('input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"]').first
    about_you_input = page.locator('input[name="name"], input[name="age"]').first

    def _after_email_ready() -> bool:
        return (
            (pwd_input.count() > 0 and pwd_input.is_visible())
            or (otp_input.count() > 0 and otp_input.is_visible())
            or about_you_input.count() > 0
            or _is_chatgpt_app_url(page.url or "")
        )

    status, err_code, err_body = _wait_until_ready_with_cf(page, mouse, _after_email_ready, timeout_seconds=50.0)
    if status == "fatal":
        return {"ok": False, "error": err_code, "detail": {"url": page.url[:200], "text": err_body, "screenshot": _save_error_shot(page)}}

    password_set = False
    if pwd_input.count() > 0 and pwd_input.is_visible():
        is_existing_login = _is_login_password_page(page)
        otc_btn = _find_one_time_code_btn(page)
        if is_existing_login and not has_known_password and otc_btn.count() > 0 and otc_btn.is_visible():
            _log("existing account login page detected without known password; switching to one-time code")
            started_at = datetime.now(timezone.utc)
            mouse.click_locator(otc_btn, timeout=5000)
            time.sleep(random.uniform(2.0, 3.5))
        else:
            _log("typing password")
            mouse.type_locator(pwd_input, password)
            password_set = True
            time.sleep(random.uniform(0.4, 0.9))
            started_at = datetime.now(timezone.utc)
            mouse.click_locator(_find_continue_btn(page))
            time.sleep(random.uniform(2.0, 3.5))
            if is_existing_login:
                otc_after = _find_one_time_code_btn(page)
                if otc_after.count() > 0 and otc_after.is_visible():
                    _log("password rejected on existing account; switching to one-time code")
                    password_set = False
                    started_at = datetime.now(timezone.utc)
                    mouse.click_locator(otc_after, timeout=5000)
                    time.sleep(random.uniform(2.0, 3.5))

    def _after_pwd_ready() -> bool:
        return (
            (otp_input.count() > 0 and otp_input.is_visible())
            or about_you_input.count() > 0
            or _is_chatgpt_app_url(page.url or "")
        )

    status, err_code, err_body = _wait_until_ready_with_cf(page, mouse, _after_pwd_ready, timeout_seconds=45.0)
    if status == "fatal":
        return {"ok": False, "error": err_code, "detail": {"url": page.url[:200], "text": err_body, "screenshot": _save_error_shot(page)}}

    already_signed_in = _is_chatgpt_app_url(page.url or "") or about_you_input.count() > 0
    if not already_signed_in and (otp_input.count() == 0 or not otp_input.is_visible()):
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=2000)[:300]
        except Exception:
            pass
        if _is_cloudflare_challenge(page):
            return {"ok": False, "error": "signup_csrf_failed_403", "detail": {"reason": "Cloudflare challenge did not clear", "url": page.url[:200], "text": body_text, "screenshot": _save_error_shot(page)}}
        if "rate" in body_text.lower() or "too many" in body_text.lower():
            return {"ok": False, "error": "rate_limit_exceeded", "detail": {"text": body_text}}
        return {"ok": False, "error": "otp_page_not_reached", "detail": {"url": page.url[:200], "text": body_text, "screenshot": _save_error_shot(page)}}

    if not already_signed_in:
        _log(f"waiting for OTP for {email} since {started_at.isoformat()}")
        excluded_codes: set[str] = set()
        otp_accepted = False
        for attempt in range(3):
            try:
                otp = mailbox.wait_for_code(email, started_at, exclude=excluded_codes)
            except Exception as exc:
                _log(f"OTP wait failed: {exc}")
                return {"ok": False, "error": "otp_not_received", "detail": {"reason": str(exc)}}
            _log(f"got OTP attempt {attempt + 1}: {otp}")
            mouse.type_locator(otp_input, otp, clear_first=attempt > 0)
            time.sleep(random.uniform(0.4, 0.8))
            cont_btn = _find_continue_btn(page)
            if cont_btn.count() > 0 and cont_btn.is_visible():
                mouse.click_locator(cont_btn)

            otp_error = False
            for _ in range(30):
                time.sleep(0.5)
                fatal = _detect_fatal_auth_error(page)
                if fatal:
                    err_code, err_body = fatal
                    _log(f"fatal auth screen after OTP: {err_code}")
                    return {
                        "ok": False,
                        "error": err_code,
                        "detail": {"url": page.url[:200], "text": err_body, "screenshot": _save_error_shot(page)},
                    }
                cur_url = page.url or ""
                if (
                    "email-verification" not in cur_url
                    or _is_chatgpt_app_url(cur_url)
                    or page.locator('input[name="name"], input[name="age"]').count() > 0
                ):
                    _log(f"OTP accepted -> {cur_url[:90]}")
                    otp_accepted = True
                    break
                try:
                    err_txt = page.locator('[role="alert"], [class*="error" i]').first.inner_text(timeout=300).lower()
                except Exception:
                    err_txt = ""
                if any(hint in err_txt for hint in ("incorrect", "invalid", "expired", "wrong", "doesn't match")):
                    _log(f"OTP rejected by page: {err_txt[:100]}")
                    otp_error = True
                    break
            if otp_accepted:
                break
            excluded_codes.add(otp)
            if hasattr(mailbox, "mark_used"):
                mailbox.mark_used(email, otp)
            if otp_error:
                resend_btn = page.locator('button:has-text("Resend"), a:has-text("Resend")').first
                if resend_btn.count() > 0 and resend_btn.is_visible():
                    started_at = datetime.now(timezone.utc)
                    mouse.click_locator(resend_btn)
                    time.sleep(1.5)

        if not otp_accepted:
            return {"ok": False, "error": "invalid_otp", "detail": {"url": page.url[:200], "screenshot": _save_error_shot(page)}}

    created = False
    if "about-you" in page.url or page.locator('input[name="name"]').count() > 0:
        created = True
        name_input = page.locator('input[name="name"], input[placeholder*="name" i]').first
        name_input.wait_for(state="visible", timeout=20000)
        mouse.type_locator(name_input, full_name)
        time.sleep(random.uniform(0.4, 0.8))

        age_input = page.locator('input[name="age"], input[placeholder*="Age" i]').first
        bday_single = page.locator('input[name="birthday"], input[placeholder*="MM/DD" i], input[placeholder*="YYYY" i]').first
        spins = page.locator('[role="spinbutton"]')
        if age_input.count() > 0 and age_input.is_visible():
            mouse.type_locator(age_input, str(2026 - year))
            time.sleep(random.uniform(0.3, 0.6))
        elif spins.count() >= 3:
            for idx in range(spins.count()):
                el = spins.nth(idx)
                aria = (el.get_attribute("aria-label") or "").lower()
                val = str(year) if "year" in aria else (f"{month:02d}" if "month" in aria else f"{day:02d}")
                mouse.click_locator(el)
                page.keyboard.type(val, delay=random.uniform(50, 120))
                time.sleep(random.uniform(0.2, 0.4))
        elif bday_single.count() > 0 and bday_single.is_visible():
            mouse.type_locator(bday_single, f"{month:02d}/{day:02d}/{year}")

        submit_btn = _find_continue_btn(page)
        if submit_btn.count() == 0 or not submit_btn.is_visible():
            submit_btn = page.locator('button[type="submit"], button:has-text("Finish"), button:has-text("Agree")').first
        _log("submitting about-you")
        mouse.click_locator(submit_btn, timeout=10000)
        time.sleep(random.uniform(3.5, 5.5))

    try:
        page.wait_for_url("**/chatgpt.com/**", timeout=25000)
    except Exception:
        pass
    fatal = _detect_fatal_auth_error(page)
    if fatal:
        err_code, err_body = fatal
        _log(f"fatal auth screen after about-you: {err_code}")
        return {
            "ok": False,
            "error": err_code,
            "created": created,
            "detail": {"url": page.url[:200], "text": err_body, "screenshot": _save_error_shot(page)},
        }
    time.sleep(random.uniform(2.5, 3.8))
    _log(f"reached post-signup page: {page.url[:90]}")

    for _ in range(3):
        cont_btn = _find_onboarding_btn(page)
        if cont_btn is not None:
            if not mouse.click_locator(cont_btn, timeout=2500, optional=True):
                break
            time.sleep(random.uniform(2.0, 3.2))
        else:
            break

    session_token = ""
    try:
        for cookie in ctx.cookies("https://chatgpt.com"):
            if cookie.get("name") == "__Secure-next-auth.session-token":
                session_token = str(cookie.get("value") or "").strip()
                break
    except Exception:
        pass
    _log(f"starting Platform OAuth (has_session_token={bool(session_token)})")

    code_verifier, code_challenge = generate_pkce()
    params = {
        "issuer": "https://auth.openai.com",
        "client_id": _OAUTH_CLIENT_ID,
        "audience": "https://api.openai.com/v1",
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    captured_code = {"code": ""}

    def on_route(route):
        url = route.request.url
        if url.startswith(_OAUTH_REDIRECT_URI):
            qs = parse_qs(urlparse(url).query)
            if qs.get("code"):
                captured_code["code"] = qs["code"][0]
            route.fulfill(status=200, body="<html><body>OK</body></html>", content_type="text/html")
            return
        route.continue_()

    page.route(f"{_OAUTH_REDIRECT_URI}*", on_route)
    page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
    last_cf_click = 0.0
    for _ in range(10):
        time.sleep(random.uniform(1.6, 2.6))
        if captured_code["code"]:
            _log("Platform OAuth code captured")
            break
        cur_url = page.url or ""
        if _is_cloudflare_challenge(page) or _find_turnstile_checkbox_point(page) is not None:
            new_click = _click_turnstile_if_ready(page, mouse, last_cf_click, min_interval=7.0)
            if new_click != last_cf_click:
                last_cf_click = new_click
                continue
        acct_el = page.locator(f"text={email}").first
        if "choose-an-account" in cur_url and acct_el.count() > 0 and acct_el.is_visible():
            _log("selecting account on choose-an-account")
            mouse.click_locator(acct_el, timeout=3000, optional=True)
            continue
        consent_btn = page.get_by_role("button", name="Continue", exact=True).first
        if consent_btn.count() == 0 or not consent_btn.is_visible():
            consent_btn = page.locator(
                'button:has-text("Continue"), button:has-text("Accept"), button:has-text("Allow")'
            ).first
        if consent_btn.count() > 0 and consent_btn.is_visible():
            _log(f"clicking OAuth button on {cur_url[:70]}")
            mouse.click_locator(consent_btn, timeout=3000, optional=True)
            continue

    if not captured_code["code"]:
        body_snip = ""
        try:
            body_snip = page.locator("body").inner_text(timeout=1500)[:200]
        except Exception:
            pass
        _log(f"Platform OAuth code missing on {page.url[:90]}: {body_snip[:100]}")
        return {
            "ok": False,
            "error": "no_auth_code",
            "created": created,
            "detail": {"url": page.url[:200], "text": body_snip, "screenshot": _save_error_shot(page)},
        }

    resp = page.request.post(
        _OAUTH_TOKEN_URL,
        data=json.dumps({
            "client_id": _OAUTH_CLIENT_ID,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": captured_code["code"],
            "redirect_uri": _OAUTH_REDIRECT_URI,
        }),
        headers={"Content-Type": "application/json"},
    )
    tok_data = resp.json() if resp.ok else {}
    access_token = str(tok_data.get("access_token") or "").strip()
    if resp.status != 200 or not access_token:
        return {
            "ok": False,
            "error": f"oauth_token_http_{resp.status}",
            "created": created,
            "detail": {"status": resp.status, "body": resp.text()[:300]},
        }

    return {
        "ok": True,
        "created": created,
        "email": email,
        "password": password if password_set else "",
        "password_set": password_set,
        "access_token": access_token,
        "refresh_token": str(tok_data.get("refresh_token") or "").strip(),
        "id_token": str(tok_data.get("id_token") or "").strip(),
        "session_token": session_token,
    }
