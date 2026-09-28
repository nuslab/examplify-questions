"""Signing in to the portal in a persistent browser profile, and keeping the session."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from playwright.sync_api import BrowserContext, Dialog, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .portal import Portal, PortalError, Site

COOKIE_DOMAIN = "examsoft.com"
SESSION_FILE = "examsoft-session.json"
"""Saved in the browser profile, next to the cookies Chromium keeps itself."""
FED_LOGIN = "#emFedLoginLink"
"""The Exam Maker panel's identity provider (SSO) login link, shown when the school has one;
`#etFedLoginLink` is the exam takers' one."""
FED_LOGIN_MS = 5_000
LOGIN_PROMPT = "Sign in to ExamSoft in the browser; the run goes on at the question bank."


@contextmanager
def signed_in(site: Site, profile: Path, timeout_ms: float) -> Iterator[Page]:
    """A page of the question bank in the profile's browser, closed on exit."""
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            profile, headless=False, no_viewport=True
        )
        session = profile / SESSION_FILE
        page = context.pages[0] if context.pages else context.new_page()
        try:
            restore_session(context, session)
            page.on("dialog", accept)
            sign_in(page, site, timeout_ms)
            save_session(context, session)
            yield page
        finally:
            with suppress(PlaywrightError):
                Portal(page, site).clear_locks()
                save_session(context, session)
            context.close()


def accept(dialog: Dialog) -> None:
    """Leaving an editor may raise its unsaved-changes prompt; every other dialog is shown."""
    if dialog.type == "beforeunload":
        with suppress(PlaywrightError):
            dialog.accept()
    else:
        print(f"  portal dialog: {dialog.message}")
        dialog.accept()


def sign_in(page: Page, site: Site, timeout_ms: float) -> None:
    """Open the question bank, signing in when the session has lapsed.

    The login page's SSO link is followed when the school has one; the sign-in itself
    waits in the browser without a timeout.
    """
    page.goto(site.questions, timeout=timeout_ms)
    if page.url.startswith(f"{site.app}/ei/"):
        return
    login_pages = f"{site.host}/GKWeb/login/"
    if not page.url.startswith(login_pages):
        page.goto(site.login, timeout=timeout_ms)
    if not page.url.startswith(login_pages):
        raise PortalError(f"{site.login} led to {page.url}; is {site.school!r} the school code?")
    print(LOGIN_PROMPT)
    with suppress(PlaywrightTimeoutError):
        page.click(FED_LOGIN, timeout=FED_LOGIN_MS)
    page.wait_for_url(f"{site.app}/ei/**", timeout=0)
    if not page.url.startswith(site.questions):
        page.goto(site.questions, timeout=timeout_ms)


def save_session(context: BrowserContext, path: Path) -> None:
    """Keep ExamSoft's session cookies, which Chromium drops at its next start."""
    cookies = [c for c in context.cookies() if c["domain"].endswith(COOKIE_DOMAIN)]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cookies), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def restore_session(context: BrowserContext, path: Path) -> None:
    if path.exists():
        with suppress(ValueError, PlaywrightError):
            context.add_cookies(json.loads(path.read_text(encoding="utf-8")))
