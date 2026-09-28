"""An ExamSoft portal's question editors, driven through Playwright."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from playwright.sync_api import BrowserContext, Page, Route
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .folders import Folder, FolderError, folders, missing_tail, resolve
from .models import CaseStudyTab, Essay, FillInTheBlank, MultipleChoice, RangeBlank, TextBlank
from .spec import content_html, stem_html
from .tags import has_tag

HOST = "https://examsoft.example.com"
LOGIN = f"{HOST}/GKWeb/login/myschool"
APP = f"{HOST}/STW-war"
SCHOOL = "myschool"
QUESTIONS = f"{APP}/ei/questions/s={SCHOOL}"
COOKIE_DOMAIN = "examsoft.com"
SESSION_FILE = "examsoft-session.json"
"""Saved in the browser profile, next to the cookies Chromium keeps itself."""
FED_LOGIN = "#emFedLoginLink"
"""The Exam Maker panel's SSO login link; `#etFedLoginLink` is the exam takers' one."""
EDITORS: dict[str, str] = {"mc": "mcq", "fitb": "fitb", "essay": "essay"}
SCORING_BOXES = {
    "partial": "#proportionateScoringMC",
    "all_or_nothing": "#enableAllThatApplyMC",
    "plus_minus": "#plusMinusScoringMC",
}
CALCULATORS = {
    "none": (False, False),
    "scientific": (False, True),
    "graphing": (True, False),
    "both": (True, True),
}
"""(graphing, scientific) checkbox states per `calculator`."""
FOLDER_RESOURCE = "0"
"""The portal's resource type of question folders (1 assessments, 2 categories, 4 rubrics)."""
MATHML = re.compile(r"<math\b.*?</math>", re.S)
SPACE_BEFORE_MATH = re.compile(r">\s+<math\b")
FORMULA_STORE = "/STW-war/sfr/"
"""Where the WIRIS server stores formula images; the portal accepts inline images only there."""
READY: Final = "domcontentloaded"
"""Editor pages are awaited by their own readiness (`loaded` in page.js): one with many
formula images never fires `load`, although every image arrives."""
LOAD_MS = 30_000
LOAD_ATTEMPTS = 3
REDIRECT_MS = 10_000
"""How long to wait for the editor's own redirect after a save."""
SCRIPT = Path(__file__).with_name("page.js").read_text(encoding="utf-8")

AnyQuestion = MultipleChoice | FillInTheBlank | Essay
Status = Literal["Draft", "Approved"]


class PortalError(RuntimeError):
    """The portal refused an action or did not respond as its editors do."""


@dataclass(frozen=True)
class Saved:
    item_id: int
    revision: int
    url: str


@dataclass(frozen=True)
class Found:
    """A question from the portal's keyword search, at its latest revision."""

    item_id: int
    revision: int
    title: str
    folder_key: str
    approved: bool
    url: str


def run(page: Page, command: str, **args: object) -> Any:  # noqa: ANN401 - JSON from the page
    return page.evaluate(SCRIPT, {"command": command, **args})


def wait(page: Page, command: str, timeout_ms: float, **args: object) -> None:
    page.wait_for_function(SCRIPT, arg={"command": command, **args}, timeout=timeout_ms)


def sign_in(page: Page, timeout_ms: float, prompt: Callable[[], None] = lambda: None) -> bool:
    """Open the question bank, signing in through SSO when the session has lapsed.

    Returns whether it signed in; `prompt` is called first. SSO prompts wait in the
    browser without a timeout.
    """
    page.goto(QUESTIONS, timeout=timeout_ms)
    if page.url.startswith(f"{APP}/ei/"):
        return False
    prompt()
    if not page.url.startswith(LOGIN):
        page.goto(LOGIN, timeout=timeout_ms)
    page.click(FED_LOGIN, timeout=timeout_ms)
    page.wait_for_url(f"{APP}/ei/**", timeout=0)
    if not page.url.startswith(QUESTIONS):
        page.goto(QUESTIONS, timeout=timeout_ms)
    return True


def save_session(context: BrowserContext, path: Path) -> None:
    """Keep ExamSoft's cookies, which are session cookies Chromium drops at its next start.

    ExamSoft asks SSO for a password sign-in each time, so without them every
    launch would prompt again; with them, a launch reuses the session until it expires.
    """
    cookies = [c for c in context.cookies() if c["domain"].endswith(COOKIE_DOMAIN)]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cookies), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def restore_session(context: BrowserContext, path: Path) -> None:
    if path.exists():
        with suppress(ValueError, PlaywrightError):
            context.add_cookies(json.loads(path.read_text(encoding="utf-8")))


class Portal:
    def __init__(self, page: Page, timeout_ms: float = 60_000) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self._folders: list[Folder] | None = None
        self._rich: dict[str, str] = {}
        """Editor id to the HTML set in it, in the open editor."""

    # Folders

    def folders(self, refresh: bool = False) -> list[Folder]:
        """Folders the account can add questions to, as the editor's folder picker lists them."""
        if self._folders is None or refresh:
            response = self.page.context.request.get(
                f"{APP}/ei/questionstree/editablefolders", timeout=self.timeout_ms
            )
            if not response.ok:
                raise PortalError(f"folder tree: HTTP {response.status}")
            self._folders = folders(response.json()["data"])
        return self._folders

    def folder(self, path: tuple[str, ...], create: bool = False) -> Folder:
        try:
            return resolve(self.folders(), path)
        except FolderError:
            if not create or any(f.path[-len(path) :] == path for f in self.folders()):
                raise
        parent, names = missing_tail(self.folders(), path)
        for name in names:
            parent = self.create_folder(parent, name)
        return parent

    def create_folder(self, parent: Folder, name: str) -> Folder:
        grandparent = next((f.key for f in self.folders() if f.path == parent.path[:-1]), "")
        self._on_app_page()
        payload = {
            "resourceName": name,
            "resourceID": parent.key,
            "resourceType": FOLDER_RESOURCE,
            "resourceOriginalParentID": grandparent,
            "description": "",
        }
        result = run(self.page, "post", url=f"{APP}/ei/resource/newResource", payload=payload)
        if result.get("status") != "EI_OK":
            raise PortalError(f"creating folder {name!r} in {parent.name}: {messages(result)}")
        created = Folder((*parent.path, name), str(result["payload"]))
        self.folders(refresh=True)
        return created

    # Search and locks

    def find(self, term: str, folder: Folder | None = None) -> list[Found]:
        """Questions whose ID, title, stem or choices contain `term`, as the portal searches.

        With `folder`, the search covers only that folder.
        """
        self._on_app_page()
        result = run(self.page, "search", term=term, folders=[folder.key] if folder else [])
        if result.get("status") != "EI_OK":
            raise PortalError(f"searching for {term!r}: {messages(result)}")
        return [
            Found(
                item_id=int(row["itemID"]),
                revision=int(row["revNum"]),
                title=row["qnTitle"] or "",
                folder_key=row["folderUID"],
                approved=bool(row["approved"]),
                url=HOST + row["editUrl"],
            )
            for row in result["rows"]
        ]

    def find_tagged(self, question_tag: str, folder: Folder) -> list[Found]:
        """The questions in `folder` whose title carries the tag."""
        return tagged_in(self.find(question_tag, folder), question_tag, folder)

    def lock(self, item_id: int, revision: int) -> bool:
        """Take the edit lock the portal takes before opening an editor; False if held."""
        self._on_app_page()
        url = f"{APP}/ei/question/refreshAndLock/s={SCHOOL},itemId={item_id},rev={revision}"
        result = run(self.page, "post", url=url, payload=None)
        match result.get("status"):
            case "EI_OK":
                return True
            case "EI_ALERT":
                return False
        raise PortalError(f"locking item {item_id}: {messages(result)}")

    def clear_locks(self) -> None:
        """Release this session's edit locks; the portal otherwise holds them until logout."""
        if run_if_loaded(self.page, "install") is not None:
            run(self.page, "post", url=f"{APP}/ei/locks/clearLocks/s={SCHOOL}", payload=None)

    def _on_app_page(self) -> None:
        if not self.page.url.startswith(f"{APP}/ei/"):
            self.page.goto(QUESTIONS, timeout=self.timeout_ms)

    # Editor

    def open_editor(self, kind: str) -> None:
        run_if_loaded(self.page, "install")
        self.page.goto(
            f"{APP}/ei/question/{kind}/create/s={SCHOOL}", timeout=self.timeout_ms, wait_until=READY
        )
        self.wait_loaded()
        run(self.page, "install")

    def wait_loaded(self, attempts: int = LOAD_ATTEMPTS) -> None:
        """Wait for the editor; reload it when the portal's own scripts fail to build it.

        An editor page intermittently throws while building its answer-choice editors,
        which then never load; the same page loads fine on another try.
        """
        for attempt in range(1, attempts + 1):
            try:
                wait(self.page, "loaded", min(self.timeout_ms, LOAD_MS))
                return
            except PlaywrightTimeoutError:
                if attempt == attempts:
                    state = run_if_loaded(self.page, "loadState")
                    raise PortalError(f"the editor did not finish loading: {state}") from None
                run_if_loaded(self.page, "install")
                self.page.reload(timeout=self.timeout_ms, wait_until=READY)

    def fill(
        self, question: AnyQuestion, folder: Folder, title: str, existing: Found | None = None
    ) -> None:
        """Fill in a new question of the question's type, or `existing`, without saving.

        An existing question must be locked to this session (`lock`) first.
        """
        if existing is None:
            self.open_editor(EDITORS[question.type])
        else:
            self.open(existing.url)
            self.wait_loaded()
            run(self.page, "install")
        self._rich = {}
        self.page.fill("#displayText", title)
        if folder.key != self.page.input_value("#folderUID"):
            self.select_folder(folder)
        self.fill_case_study(question.case_study or [])
        match question:
            case MultipleChoice():
                self.fill_choices(question)
            case FillInTheBlank():
                self.fill_blanks(question)
            case Essay():
                if question.char_limit is not None:
                    self.page.fill("#essayCharLim", str(question.char_limit))
        if not isinstance(question, FillInTheBlank):
            self.set_rich("questionRichText", stem_html(question))
        self.page.fill("#weight", number(question.points))
        self.page.fill("#randomGroup", question.group or "")
        self.page.keyboard.press("Escape")  # Close the group field's autocomplete list.
        self.page.fill(
            "#cutScore", "" if question.cut_score is None else number(question.cut_score)
        )
        graphing, scientific = CALCULATORS[question.calculator]
        self.page.locator("#graphingCalculatorChk").set_checked(graphing)
        self.page.locator("#scientificCalculatorChk").set_checked(scientific)
        self.page.locator("#spreadsheetChk").set_checked(question.spreadsheet)
        self.page.fill("#comment", question.rationale or "")
        self.settle_rich()

    def set_rich(self, editor_id: str, html: str) -> None:
        html = self.render_math(html)
        self._rich[editor_id] = html
        run(self.page, "setRich", id=editor_id, html=html)

    def render_math(self, html: str) -> str:
        """Replace each MathML formula with a formula image, as the formula editor makes."""

        def image(match: re.Match[str]) -> str:
            made = run(self.page, "mathImage", mathml=match.group(0))
            if FORMULA_STORE not in made["src"]:
                raise PortalError(
                    f"formula image for {match.group(0)[:80]}...: {made['src'][:200]}"
                )
            return str(made["html"])

        # A space between a tag and a formula (`<strong>State:</strong> <img>`) is dropped
        # when the portal saves the question; a non-breaking one is kept.
        return MATHML.sub(image, SPACE_BEFORE_MATH.sub(">&nbsp;<math", html))

    def settle_rich(self, attempts: int = 5) -> None:
        """Make sure every rich-text editor still holds what was set in it.

        After a choice is added or removed the editor reloads its editors from their
        textareas, asynchronously; one could land after the data set here and blank it.
        """
        ids = list(self._rich)
        expected = [run(self.page, "text", html=self._rich[i]) for i in ids]
        for _ in range(attempts):
            actual = run(self.page, "richTexts", ids=ids)
            drifted = [i for i, want, got in zip(ids, expected, actual, strict=True) if want != got]
            if not drifted:
                return
            for editor_id in drifted:
                run(self.page, "setRich", id=editor_id, html=self._rich[editor_id])
            self.page.wait_for_timeout(300)
        raise PortalError(f"editors {drifted} did not keep their text")

    def select_folder(self, folder: Folder) -> None:
        self.page.click("#selectQuestionFolderOpen")
        wait(self.page, "folderLoaded", self.timeout_ms, key=folder.key)
        run(self.page, "selectFolder", key=folder.key)
        self.page.wait_for_function(
            "key => document.querySelector('#folderUID').value === key",
            arg=folder.key,
            timeout=self.timeout_ms,
        )

    def fill_case_study(self, tabs: list[CaseStudyTab]) -> None:
        titles = "input[name='caseStudyTitle[]']"
        present = self.page.locator(titles).count()
        if not tabs:
            if present:  # The button reads "Remove Case Study" once there is one.
                self.page.click("#addCaseStudy")
                self.page.locator(".ui-dialog:visible button", has_text="Yes").click()
                self.page.wait_for_selector(titles, state="detached", timeout=self.timeout_ms)
            return
        if not present:
            self.page.click("#addCaseStudy")  # Opens the artefact with its first tab.
            self.page.wait_for_selector(titles, state="attached", timeout=self.timeout_ms)
        while (count := self.page.locator(titles).count()) < len(tabs):
            self.page.click("#addCaseStudyTab")
            self.page.wait_for_function(
                f'n => document.querySelectorAll("{titles}").length > n', arg=count
            )
        while (count := self.page.locator(titles).count()) > len(tabs):
            self.page.locator(".ui-tabs-nav li span.removeCaseStudyClass").last.click()
            self.page.wait_for_function(
                f'n => document.querySelectorAll("{titles}").length < n', arg=count
            )
        numbers = [
            number.removeprefix("caseStudy-title")
            for number in self.page.locator(titles).evaluate_all("inputs => inputs.map(i => i.id)")
        ]
        editors = [f"caseStudy-fragment-{n}" for n in numbers]
        wait(self.page, "editorsReady", self.timeout_ms, ids=editors)
        for n, editor_id, tab in zip(numbers, editors, tabs, strict=True):
            # The tab title is set through its edit icon's dialog, as the portal does.
            self.page.click(f"#editCSTab{n}")
            self.page.fill("#tab_title", tab.title)
            self.page.locator(".ui-dialog:visible button", has_text="Save").click()
            self.set_rich(editor_id, content_html(tab))

    def fill_choices(self, question: MultipleChoice) -> None:
        rows = "#mcqChoices tr.mcqRow"
        while (count := self.page.locator(rows).count()) < len(question.choices):
            self.page.click("#addMCQChoice")
            self.page.wait_for_function(
                f"n => document.querySelectorAll('{rows}').length > n", arg=count
            )
        while (count := self.page.locator(rows).count()) > len(question.choices):
            self.page.locator(rows).last.locator("a.removeChoice img").click()
            self.page.wait_for_function(
                f"n => document.querySelectorAll('{rows}').length < n", arg=count
            )
        ids: list[str] = run(self.page, "choiceIds")
        wait(self.page, "editorsReady", self.timeout_ms, ids=ids)
        for choice_id, choice in zip(ids, question.choices, strict=True):
            self.set_rich(choice_id, content_html(choice))
            row = self.page.locator(f'{rows}[choiceuid="{choice_id}"]')
            row.locator("input[name='correctBool[]']").set_checked(choice.correct)
            row.locator("input[name='locked[]']").set_checked(choice.locked)
        # The three scoring boxes are mutually exclusive; clear before setting the one.
        scoring = question.effective_scoring
        for mode, box in SCORING_BOXES.items():
            if mode != scoring:
                self.page.locator(box).set_checked(False)
        if scoring is not None:
            self.page.locator(SCORING_BOXES[scoring]).set_checked(True)
        self.page.locator("#randomizeChoices1").set_checked(question.randomize_choices)

    def fill_blanks(self, question: FillInTheBlank) -> None:
        # Each button registers the blank with the server's copy of the question and
        # inserts its placeholder into the stem, which is then replaced whole. An
        # existing question keeps its blanks, which must still fit the spec.
        wanted = ["RANGE" if isinstance(b, RangeBlank) else "BLANK" for b in question.blanks]
        present = [row["type"] for row in run(self.page, "blanks")]
        if present and present != wanted:
            raise PortalError(
                f"the question has blanks {present} and the spec {wanted}; changing the"
                " blanks of an existing question is not supported"
            )
        for blank in question.blanks if not present else []:
            before = len(run(self.page, "blanks"))
            self.page.click("#addNewRange" if isinstance(blank, RangeBlank) else "#addNewBlank")
            self.page.wait_for_function(
                "n => document.querySelectorAll(\"#blanksTable input[name='blankTypes[]']\")"
                ".length > n",
                arg=before,
            )
        rows = run(self.page, "blanks")
        sequences = [row["sequence"] for row in rows]
        if sequences != [str(n) for n in range(1, len(question.blanks) + 1)]:
            raise PortalError(f"expected blanks numbered 1..{len(question.blanks)}: {sequences}")
        self.set_rich("questionRichText", stem_html(question))
        # Fields by row: a new question's ids (`blankText_1`) differ from an edit page's.
        rows_with_type = self.page.locator("#blanksTable tr").filter(
            has=self.page.locator("input[name='blankTypes[]']")
        )
        for index, blank in enumerate(question.blanks):
            fields = rows_with_type.nth(index).locator("[name='blankTexts[]']")
            match blank:
                case TextBlank():
                    fields.nth(0).fill("|".join(blank.answers))
                case RangeBlank():
                    fields.nth(0).fill(number(blank.range[0]))
                    fields.nth(1).fill(number(blank.range[1]))
        self.page.locator("#proportionateScoring1").set_checked(question.partial_credit)

    def save(
        self, question: AnyQuestion, status: Status, folder: Folder, existing: Found | None = None
    ) -> Saved:
        """Press Save (Draft) or Approve; the question exists once this returns.

        On an existing question's edit page the buttons save it in place.
        """
        kind = EDITORS[question.type]
        action, button = ("editSave", "Edit") if existing is not None else ("save", "")
        captured: dict[str, Any] = {}

        def capture(route: Route) -> None:
            response = route.fetch()
            captured["status"] = response.status
            captured["body"] = response.text()
            route.fulfill(response=response)

        self.settle_rich()
        pattern = f"**/ei/question/{kind}/{action}{status}"
        self.page.route(pattern, capture)
        try:
            self.page.click(f"#{kind}{button}{'SaveItem' if status == 'Draft' else 'ApproveItem'}")
            deadline = time.monotonic() + self.timeout_ms / 1000
            while "body" not in captured:
                shown = run_if_loaded(self.page, "messages")
                if shown:
                    raise PortalError("; ".join(shown))
                if time.monotonic() > deadline:
                    raise PortalError("the editor sent no save request")
                self.page.wait_for_timeout(200)
        finally:
            self.page.unroute(pattern, capture)
        try:
            result = json.loads(captured["body"])
        except ValueError as error:
            raise PortalError(f"save: HTTP {captured['status']}, not JSON") from error
        if result.get("status") != "EI_OK":
            raise PortalError(messages(result))
        payload = result.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        fallback = existing or Found(0, 0, "", "", False, "")
        item_id = int(payload.get("itemId") or fallback.item_id)
        revision = int(payload.get("revNum") or fallback.revision)
        if not item_id:
            raise PortalError(f"save: no item id in {str(result)[:200]}")
        return Saved(item_id, revision, edit_url(kind, item_id, revision))

    def read_back(
        self, question: AnyQuestion, folder: Folder, title: str, saved: Saved
    ) -> list[str]:
        """Where the saved question, as its edit page shows it, differs from the spec."""
        # Save reloads into the edit page; Approve stays put, so open it then.
        try:
            self.page.wait_for_url(f"{saved.url}*", timeout=REDIRECT_MS, wait_until=READY)
        except PlaywrightTimeoutError:
            self.open(saved.url)
        self.wait_loaded()
        run(self.page, "install")
        return self.differences(question, folder, title, run(self.page, "state"))

    def open(self, url: str) -> None:
        run_if_loaded(self.page, "install")
        self.page.goto(url, timeout=self.timeout_ms, wait_until=READY)

    def differences(
        self, question: AnyQuestion, folder: Folder, title: str, state: dict[str, Any]
    ) -> list[str]:
        """Compare what the editor holds with the spec; stems and choices by their text."""

        def text(html: str) -> str:
            return str(run(self.page, "text", html=MATHML.sub("", html)))  # Formulas are images.

        texts = {
            "stem": text(stem_html(question)),
            "caseStudy": [text(content_html(tab)) for tab in question.case_study or []],
            "choices": [text(content_html(choice)) for choice in getattr(question, "choices", [])],
        }
        return differences(question, folder, title, state, texts)


def differences(
    question: AnyQuestion,
    folder: Folder,
    title: str,
    state: dict[str, Any],
    texts: dict[str, Any],
) -> list[str]:
    found: list[str] = []

    def check(name: str, expected: object, actual: object) -> None:
        if expected != actual:
            found.append(f"{name}: expected {expected!r}, found {actual!r}")

    check("folder", folder.key, state["folderKey"])
    check("title", title, state["title"])
    check("points", question.points, as_float(state["weight"]))
    check("group", question.group or "", state["group"] or "")
    check("cut score", question.cut_score, as_float(state["cutScore"]))
    check("rationale", (question.rationale or "").strip(), (state["rationale"] or "").strip())
    check("stem", texts["stem"], state["stem"])
    expected_tabs = [
        {"title": tab.title, "text": text}
        for tab, text in zip(question.case_study or [], texts["caseStudy"], strict=True)
    ]
    check("case study", expected_tabs, state["caseStudy"])
    options = state["options"]
    graphing, scientific = CALCULATORS[question.calculator]
    check("graphing calculator", graphing, options["graphing"])
    check("scientific calculator", scientific, options["scientific"])
    check("spreadsheet", question.spreadsheet, options["spreadsheet"])
    match question:
        case MultipleChoice():
            scoring = question.effective_scoring
            check("partial credit", scoring == "partial", options["partial"])
            # The editor ticks and locks Select All That Apply along with +/- Partial Credit.
            all_that_apply = scoring in ("all_or_nothing", "plus_minus")
            check("select all that apply", all_that_apply, options["allThatApply"])
            check("+/- partial credit", scoring == "plus_minus", options["plusMinus"])
            check("randomize choices", question.randomize_choices, options["randomize"])
            expected_choices = [
                {"text": text, "correct": choice.correct, "locked": choice.locked}
                for text, choice in zip(texts["choices"], question.choices, strict=True)
            ]
            check("choices", expected_choices, state["choices"])
        case FillInTheBlank():
            check("partial credit", question.partial_credit, options["partial"])
            check("blanks in stem", len(question.blanks), state["stemBlanks"])
            check(
                "blanks",
                [blank_state(b) for b in question.blanks],
                [normal_blank(b) for b in state["blanks"]],
            )
        case Essay():
            check("character limit", question.char_limit, as_int(state["charLimit"]))
    return found


def tagged_in(results: list[Found], question_tag: str, folder: Folder) -> list[Found]:
    """Search results that are the tag's question: the exact `[tag]` in the title (the
    search also matches stems and choices), in the folder itself (not a subfolder)."""
    return [
        found
        for found in results
        if has_tag(found.title, question_tag) and found.folder_key == folder.key
    ]


def edit_url(kind: str, item_id: int, revision: int) -> str:
    return f"{APP}/ei/question/{kind}/edit/s={SCHOOL},id={item_id},rev={revision}"


def blank_state(blank: TextBlank | RangeBlank) -> tuple[str, tuple[str | float, ...]]:
    if isinstance(blank, TextBlank):
        return ("BLANK", tuple(blank.answers))
    return ("RANGE", blank.range)


def normal_blank(row: dict[str, Any]) -> tuple[str, tuple[str | float, ...]]:
    """An editor blank row; saved text answers come back with a trailing `|`."""
    if row["type"] == "RANGE":
        return ("RANGE", tuple(float(value) for value in row["values"]))
    return (row["type"], tuple(a for a in "|".join(row["values"]).split("|") if a))


def run_if_loaded(page: Page, command: Literal["install", "messages", "loadState"]) -> Any:  # noqa: ANN401
    """Run a command if the page is an editor; mid-navigation, there is nothing to run it in."""
    try:
        if page.evaluate("() => !!(window.EIUtil && window.jQuery)"):
            return run(page, command)
    except PlaywrightError:
        pass
    return None


def messages(result: dict[str, Any]) -> str:
    items = result.get("messages") or result.get("payload") or []
    if not isinstance(items, list):
        items = [items]
    texts = [
        item.get("message", str(item)) if isinstance(item, dict) else str(item) for item in items
    ]
    plain = [re.sub(r"<[^>]+>", "", text) for text in texts]
    return "; ".join(plain) or f"status {result.get('status')}"


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def as_float(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def as_int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None
