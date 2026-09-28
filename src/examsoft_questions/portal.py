"""An ExamSoft portal's question editors, driven through Playwright."""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .compare import differences
from .folders import Folder, FolderError, flatten_tree, missing_tail, resolve
from .models import (
    CALCULATORS,
    CaseStudyTab,
    Essay,
    FillInTheBlank,
    MultipleChoice,
    Question,
    RangeBlank,
    TextBlank,
)
from .render import content_html, stem_html, strip_tags
from .tags import tagged_in

LOGIN_PATH = re.compile(r"/GKWeb/login/([^/]+)/?")
EDITOR_KINDS: dict[str, str] = {"mc": "mcq", "fitb": "fitb", "essay": "essay"}
"""The portal's editor, in its URLs and button ids, per question `type`."""
SCORING_BOXES = {
    "partial": "#proportionateScoringMC",
    "all_or_nothing": "#enableAllThatApplyMC",
    "plus_minus": "#plusMinusScoringMC",
}
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

SaveStatus = Literal["Draft", "Approved"]


class PortalError(RuntimeError):
    """The portal refused an action or did not respond as its editors do."""


@dataclass(frozen=True)
class Site:
    """An institution's ExamSoft portal: its host and school code."""

    host: str
    school: str

    @classmethod
    def from_login_url(cls, url: str) -> Site:
        """The site of an Exam Maker login page, such as `https://HOST/GKWeb/login/SCHOOL`."""
        parts = urlsplit(url)
        path = LOGIN_PATH.fullmatch(parts.path)
        if parts.scheme != "https" or not parts.netloc or not path:
            raise ValueError(
                f"{url!r} is not an ExamSoft login page, https://HOST/GKWeb/login/SCHOOL"
            )
        return cls(f"https://{parts.netloc}", path[1].lower())

    @property
    def app(self) -> str:
        return f"{self.host}/STW-war"

    @property
    def login(self) -> str:
        return f"{self.host}/GKWeb/login/{self.school}"

    @property
    def questions(self) -> str:
        return f"{self.app}/ei/questions/s={self.school}"


@dataclass(frozen=True)
class Saved:
    item_id: int
    revision: int
    url: str


@dataclass(frozen=True)
class Item:
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


class Portal:
    def __init__(self, page: Page, site: Site, timeout_ms: float = 60_000) -> None:
        self.page = page
        self.site = site
        self.timeout_ms = timeout_ms
        self._folders: list[Folder] | None = None
        self._rich: dict[str, str] = {}
        """Editor id to the HTML set in it, in the open editor."""

    # Folders

    def folders(self, refresh: bool = False) -> list[Folder]:
        """Folders the account can add questions to, as the editor's folder picker lists them."""
        if self._folders is None or refresh:
            response = self.page.context.request.get(
                f"{self.site.app}/ei/questionstree/editablefolders", timeout=self.timeout_ms
            )
            if not response.ok:
                raise PortalError(f"folder tree: HTTP {response.status}")
            self._folders = flatten_tree(response.json()["data"])
        return self._folders

    def folder(self, path: tuple[str, ...], create: bool = False) -> Folder:
        try:
            return resolve(self.folders(), path)
        except FolderError:
            if not create or any(f.ends_with(path) for f in self.folders()):
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
        result = run(
            self.page, "post", url=f"{self.site.app}/ei/resource/newResource", payload=payload
        )
        if result.get("status") != "EI_OK":
            raise PortalError(f"creating folder {name!r} in {parent.name}: {messages(result)}")
        created = Folder((*parent.path, name), str(result["payload"]))
        self.folders(refresh=True)
        return created

    # Search and locks

    def find_tagged(self, tag: str, folder: Folder) -> list[Item]:
        """The questions in `folder` whose title carries the tag."""
        # The keyword search also matches IDs, stems and choices.
        self._on_app_page()
        result = run(self.page, "search", term=tag, folders=[folder.key])
        if result.get("status") != "EI_OK":
            raise PortalError(f"searching for {tag!r}: {messages(result)}")
        found = [
            Item(
                item_id=int(row["itemID"]),
                revision=int(row["revNum"]),
                title=row["qnTitle"] or "",
                folder_key=row["folderUID"],
                approved=bool(row["approved"]),
                url=self.site.host + row["editUrl"],
            )
            for row in result["rows"]
        ]
        return tagged_in(found, tag, folder)

    def lock(self, item_id: int, revision: int) -> bool:
        """Take the edit lock the portal takes before opening an editor; False if held."""
        self._on_app_page()
        site = self.site
        url = (
            f"{site.app}/ei/question/refreshAndLock/s={site.school},itemId={item_id},rev={revision}"
        )
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
            run(
                self.page,
                "post",
                url=f"{self.site.app}/ei/locks/clearLocks/s={self.site.school}",
                payload=None,
            )

    def _on_app_page(self) -> None:
        if not self.page.url.startswith(f"{self.site.app}/ei/"):
            self.page.goto(self.site.questions, timeout=self.timeout_ms)

    # Editor

    def wait_loaded(self, attempts: int = LOAD_ATTEMPTS) -> None:
        """Wait for the editor and install page.js in it; reload the editor when the portal's
        own scripts fail to build it.

        An editor page intermittently throws while building its answer-choice editors,
        which then never load; the same page loads fine on another try.
        """
        for attempt in range(1, attempts + 1):
            try:
                wait(self.page, "loaded", min(self.timeout_ms, LOAD_MS))
                run(self.page, "install")
                return
            except PlaywrightTimeoutError:
                if attempt == attempts:
                    state = run_if_loaded(self.page, "loadDiagnostics")
                    raise PortalError(f"the editor did not finish loading: {state}") from None
                run_if_loaded(self.page, "install")
                self.page.reload(timeout=self.timeout_ms, wait_until=READY)

    def fill(
        self, question: Question, folder: Folder, title: str, existing: Item | None = None
    ) -> None:
        """Fill in a new question of the question's type, or `existing`, without saving.

        An existing question must be locked to this session (`lock`) first.
        """
        kind = EDITOR_KINDS[question.type]
        self.open(
            existing.url
            if existing
            else f"{self.site.app}/ei/question/{kind}/create/s={self.site.school}"
        )
        self.wait_loaded()
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
        self.set_spreadsheet(question.spreadsheet)
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

    def set_spreadsheet(self, enabled: bool) -> None:
        box = self.page.locator("#spreadsheetChk")
        if box.is_checked() == enabled:
            return
        box.click()
        if not enabled:  # Unticking asks to confirm removing the spreadsheet.
            self.page.click("#removeESSOk")
        expect(box).to_be_checked(checked=enabled, timeout=self.timeout_ms)

    def select_folder(self, folder: Folder) -> None:
        self.page.click("#selectQuestionFolderOpen")
        wait(self.page, "folderLoaded", self.timeout_ms, key=folder.key)
        run(self.page, "selectFolder", key=folder.key)
        expect(self.page.locator("#folderUID")).to_have_value(folder.key, timeout=self.timeout_ms)

    def fill_case_study(self, tabs: list[CaseStudyTab]) -> None:
        titles = self.page.locator("input[name='caseStudyTitle[]']")
        present = titles.count()
        if not tabs:
            if present:  # The button reads "Remove Case Study" once there is one.
                self.page.click("#addCaseStudy")
                self.page.locator(".ui-dialog:visible button", has_text="Yes").click()
                expect(titles).to_have_count(0, timeout=self.timeout_ms)
            return
        if not present:
            self.page.click("#addCaseStudy")  # Opens the artefact with its first tab.
            expect(titles).to_have_count(1, timeout=self.timeout_ms)
        while (count := titles.count()) < len(tabs):
            self.page.click("#addCaseStudyTab")
            expect(titles).to_have_count(count + 1, timeout=self.timeout_ms)
        while (count := titles.count()) > len(tabs):
            self.page.locator(".ui-tabs-nav li span.removeCaseStudyClass").last.click()
            expect(titles).to_have_count(count - 1, timeout=self.timeout_ms)
        numbers = [
            number.removeprefix("caseStudy-title")
            for number in titles.evaluate_all("inputs => inputs.map(i => i.id)")
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
        rows = self.page.locator("#mcqChoices tr.mcqRow")
        while (count := rows.count()) < len(question.choices):
            self.page.click("#addMCQChoice")
            expect(rows).to_have_count(count + 1, timeout=self.timeout_ms)
        while (count := rows.count()) > len(question.choices):
            rows.last.locator("a.removeChoice img").click()
            expect(rows).to_have_count(count - 1, timeout=self.timeout_ms)
        ids: list[str] = run(self.page, "choiceIds")
        wait(self.page, "editorsReady", self.timeout_ms, ids=ids)
        for choice_id, choice in zip(ids, question.choices, strict=True):
            self.set_rich(choice_id, content_html(choice))
            row = self.page.locator(f'#mcqChoices tr.mcqRow[choiceuid="{choice_id}"]')
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
        types = self.page.locator("#blanksTable input[name='blankTypes[]']")
        for blank in question.blanks if not present else []:
            before = types.count()
            self.page.click("#addNewRange" if isinstance(blank, RangeBlank) else "#addNewBlank")
            expect(types).to_have_count(before + 1, timeout=self.timeout_ms)
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

    def save(self, question: Question, status: SaveStatus, existing: Item | None = None) -> Saved:
        """Press Save (Draft) or Approve; the question exists once this returns.

        On an existing question's edit page the buttons save it in place.
        """
        kind = EDITOR_KINDS[question.type]
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
        payload = result.get("payload")
        ids = payload if isinstance(payload, dict) else {}
        item_id = int(ids.get("itemId") or (existing.item_id if existing else 0))
        revision = int(ids.get("revNum") or (existing.revision if existing else 0))
        if not item_id:
            raise PortalError(f"save: no item id in {str(result)[:200]}")
        site = self.site
        url = f"{site.app}/ei/question/{kind}/edit/s={site.school},id={item_id},rev={revision}"
        return Saved(item_id, revision, url)

    def read_back(self, question: Question, folder: Folder, title: str, url: str) -> list[str]:
        """Where the saved question, as its edit page shows it, differs from the spec."""
        # Save reloads into the edit page; Approve stays put, so open it then.
        try:
            self.page.wait_for_url(f"{url}*", timeout=REDIRECT_MS, wait_until=READY)
        except PlaywrightTimeoutError:
            self.open(url)
        self.wait_loaded()
        return self.editor_differences(question, folder, title)

    def open(self, url: str) -> None:
        run_if_loaded(self.page, "install")
        self.page.goto(url, timeout=self.timeout_ms, wait_until=READY)

    def editor_differences(self, question: Question, folder: Folder, title: str) -> list[str]:
        """Compare what the open editor holds with the spec; stems and choices by their text."""

        def text(html: str) -> str:
            return str(run(self.page, "text", html=MATHML.sub("", html)))  # Formulas are images.

        texts = {
            "stem": text(stem_html(question)),
            "caseStudy": [text(content_html(tab)) for tab in question.case_study or []],
            "choices": [text(content_html(choice)) for choice in getattr(question, "choices", [])],
        }
        return differences(question, folder, title, run(self.page, "state"), texts)


def run_if_loaded(page: Page, command: Literal["install", "messages", "loadDiagnostics"]) -> Any:  # noqa: ANN401
    """Run a command if the page is an editor; mid-navigation, there is nothing to run it in."""
    with suppress(PlaywrightError):
        if page.evaluate("() => !!(window.EIUtil && window.jQuery)"):
            return run(page, command)
    return None


def messages(result: dict[str, Any]) -> str:
    items = result.get("messages") or result.get("payload") or []
    if not isinstance(items, list):
        items = [items]
    texts = [
        item.get("message", str(item)) if isinstance(item, dict) else str(item) for item in items
    ]
    plain = [strip_tags(text) for text in texts]
    return "; ".join(plain) or f"status {result.get('status')}"


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
