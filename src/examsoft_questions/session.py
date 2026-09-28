"""Signing in to the portal in a persistent browser profile, and keeping the session."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from playwright.sync_api import BrowserContext, Dialog, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .portal import APP, HOST, QUESTIONS, Portal

LOGIN = f"{HOST}/GKWeb/login/myschool"
COOKIE_DOMAIN = "examsoft.com"
SESSION_FILE = "examsoft-session.json"
"""Saved in the browser profile, next to the cookies Chromium keeps itself."""
FED_LOGIN = "#emFedLoginLink"
"""The Exam Maker panel's SSO login link; `#etFedLoginLink` is the exam takers' one."""
LOGIN_PROMPT = "Signing in through SSO. Complete any SSO prompts in the browser."


@contextmanager
def signed_in(profile: Path, timeout_ms: float) -> Iterator[Page]:
    """A page of the question bank in the profile's browser, closed on exit."""
    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            profile, headless=False, no_viewport=True
        )
        session = profile / SESSION_FILE
        page = context.pages[0] if context.pages else context.new_page()
        try:
            restore_session(context, session)
            page.on("dialog", accept)
            sign_in(page, timeout_ms)
            save_session(context, session)
            yield page
        finally:
            with suppress(PlaywrightError):
                Portal(page).clear_locks()
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


def sign_in(page: Page, timeout_ms: float) -> None:
    """Open the question bank, signing in through SSO when the session has lapsed.

    SSO prompts wait in the browser without a timeout.
    """
    page.goto(QUESTIONS, timeout=timeout_ms)
    if page.url.startswith(f"{APP}/ei/"):
        return
    print(LOGIN_PROMPT)
    if not page.url.startswith(LOGIN):
        page.goto(LOGIN, timeout=timeout_ms)
    page.click(FED_LOGIN, timeout=timeout_ms)
    page.wait_for_url(f"{APP}/ei/**", timeout=0)
    if not page.url.startswith(QUESTIONS):
        page.goto(QUESTIONS, timeout=timeout_ms)


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
