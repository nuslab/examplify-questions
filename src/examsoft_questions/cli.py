"""Create ExamSoft questions from a YAML spec."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Dialog, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .examsoft import (
    EDITORS,
    SESSION_FILE,
    AnyQuestion,
    Found,
    Portal,
    PortalError,
    Status,
    restore_session,
    save_session,
    sign_in,
)
from .folders import Folder, FolderError
from .markdown import PaperError, convert, dump
from .models import Essay, FillInTheBlank, MultipleChoice
from .spec import SpecError, load
from .tags import tag, tagged_title

LOGIN_PROMPT = "Signing in through SSO. Complete any SSO prompts in the browser."


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.timeout_ms = args.timeout * 1000 if "timeout" in args else 60_000
    try:
        return int(args.handler(args))
    except (SpecError, FolderError, PortalError, PaperError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="examsoft-questions", description=__doc__.split("\n")[0])
    commands = root.add_subparsers(required=True)

    convert = commands.add_parser(
        "import-md", help="convert a paper in pandoc Markdown into a spec (offline)"
    )
    convert.add_argument("paper", type=Path, help="directory with questions/ and solutions/")
    convert.add_argument(
        "--config", type=Path, help="ExamSoft settings (default: PAPER/examsoft.yaml)"
    )
    convert.add_argument("-o", "--output", type=Path, help="write the spec here, not to stdout")
    convert.set_defaults(handler=cmd_import_md)

    validate = commands.add_parser("validate", help="check a spec without signing in")
    validate.add_argument("spec", type=Path)
    validate.set_defaults(handler=cmd_validate)

    listing = commands.add_parser("folders", help="list the question folders you can add to")
    listing.add_argument("--match", default="", help="only folders whose path contains this")
    browser_options(listing)
    listing.set_defaults(handler=cmd_folders)

    create = commands.add_parser("create", help="create the spec's questions in ExamSoft")
    create.add_argument("spec", type=Path)
    create.add_argument("--only", nargs="+", metavar="ID", help="create only these question ids")
    create.add_argument(
        "--approve", action="store_true", help="press Approve instead of Save (draft)"
    )
    create.add_argument(
        "--dry-run",
        action="store_true",
        help="fill each editor and check it against the spec, but never save",
    )
    create.add_argument(
        "--pause", action="store_true", help="wait for Enter before saving or leaving each editor"
    )
    create.add_argument(
        "--create-folders", action="store_true", help="create missing folders at the path's end"
    )
    browser_options(create)
    create.set_defaults(handler=cmd_create)

    update = commands.add_parser(
        "update", help="bring the spec's questions in ExamSoft up to date with the spec"
    )
    update.add_argument("spec", type=Path)
    update.add_argument("--only", nargs="+", metavar="ID", help="update only these question ids")
    update.add_argument(
        "--approve",
        action="store_true",
        help="approve updated drafts too (approved questions are always approved again)",
    )
    update.add_argument(
        "--dry-run", action="store_true", help="refill each differing editor, but never save"
    )
    update.add_argument(
        "--pause", action="store_true", help="wait for Enter before saving or leaving each editor"
    )
    browser_options(update)
    update.set_defaults(handler=cmd_update)

    verify = commands.add_parser(
        "verify", help="find the spec's questions in ExamSoft and compare them with the spec"
    )
    verify.add_argument("spec", type=Path)
    verify.add_argument("--only", nargs="+", metavar="ID", help="verify only these question ids")
    browser_options(verify)
    verify.set_defaults(handler=cmd_verify)
    return root


def browser_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--profile", default="../data/examsoft-profile")
    command.add_argument("--timeout", type=int, default=60, help="seconds per page action")


def cmd_import_md(args: argparse.Namespace) -> int:
    text = dump(convert(args.paper, args.config))
    if args.output is None:
        sys.stdout.write(text)
        return 0
    args.output.write_text(text, encoding="utf-8")
    cmd_validate(argparse.Namespace(spec=args.output))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    spec = load(args.spec)
    for question in spec.questions:
        print(f"{question.id:<20} {describe(question)}  [{question.folder}]")
    print(f"{len(spec.questions)} questions are valid.")
    return 0


def cmd_folders(args: argparse.Namespace) -> int:
    with signed_in(args) as page:
        for folder in Portal(page, args.timeout_ms).folders():
            if args.match.lower() in folder.name.lower():
                print(folder.name)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    status: Status = "Approved" if args.approve else "Draft"
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        for target in targets(portal, questions, args.create_folders and not args.dry_run):
            question, folder, title = target.question, target.folder, target.title
            existing = portal.find_tagged(target.tag, folder)
            if existing:
                print(f"{target.label}: exists as item {item_ids(existing)}; skipped")
                continue
            print(f"{target.label}: {describe(question)} -> {folder.name}")
            portal.fill(question, folder, title)
            if args.dry_run:
                problems += report(portal.differences(question, folder, title))
                pause(args, "Enter leaves this editor without saving")
                continue
            pause(args, f"Enter presses {'Approve' if args.approve else 'Save'}")
            saved = portal.save(question, status)
            print(f"  created item {saved.item_id} rev {saved.revision} ({status}): {saved.url}")
            problems += report(portal.read_back(question, folder, title, saved.url))
    return outcome(problems)


def cmd_verify(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        for target in targets(portal, questions):
            existing = portal.find_tagged(target.tag, target.folder)
            if not existing:
                print(f"{target.label}: not in ExamSoft")
                problems += 1
                continue
            if len(existing) > 1:
                print(f"{target.label}: several items carry this tag: {item_ids(existing)}")
                problems += 1
            for found in existing:
                if not portal.lock(found.item_id, found.revision):
                    print(
                        f"{target.label}: item {found.item_id} is open in another session; skipped"
                    )
                    problems += 1
                    continue
                portal.open(found.url)
                differences = portal.read_back(
                    target.question, target.folder, target.title, found.url
                )
                state = "Approved" if found.approved else "Draft"
                verdict = "" if differences else ", matches the spec"
                print(
                    f"{target.label}: item {found.item_id} rev {found.revision} ({state}){verdict}"
                )
                problems += report(differences)
    return outcome(problems)


def cmd_update(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        for target in targets(portal, questions):
            question, folder, title = target.question, target.folder, target.title
            existing = portal.find_tagged(target.tag, folder)
            if len(existing) != 1:
                several = f"several items carry this tag: {item_ids(existing)}"
                print(f"{target.label}: {several if existing else 'not in ExamSoft'}; skipped")
                problems += 1
                continue
            item = existing[0]
            if not portal.lock(item.item_id, item.revision):
                print(f"{target.label}: item {item.item_id} is open in another session; skipped")
                problems += 1
                continue
            portal.open(item.url)
            before = portal.read_back(question, folder, title, item.url)
            if not before:
                print(f"{target.label}: item {item.item_id} is up to date")
                continue
            print(f"{target.label}: item {item.item_id} differs:")
            report(before)
            portal.fill(question, folder, title, item)
            if args.dry_run:
                problems += report(portal.differences(question, folder, title))
                pause(args, "Enter leaves this editor without saving")
                continue
            status: Status = "Approved" if args.approve or item.approved else "Draft"
            pause(args, f"Enter presses {'Approve' if status == 'Approved' else 'Save'}")
            saved = portal.save(question, status, item)
            revision = "new revision" if saved.revision != item.revision else "same revision"
            print(f"  updated item {saved.item_id} rev {saved.revision}, {revision} ({status})")
            problems += report(portal.read_back(question, folder, title, saved.url))
    return outcome(problems)


@dataclass(frozen=True)
class Target:
    question: AnyQuestion
    folder: Folder
    tag: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.question.id} [{self.tag}]"


def targets(portal: Portal, questions: list[AnyQuestion], create: bool = False) -> list[Target]:
    """Resolve every folder before any question is touched, so a bad path changes nothing."""
    resolved = []
    for question in questions:
        try:
            folder = portal.folder(question.folder_path, create=create)
        except FolderError as error:
            hint = "" if create or "several" in str(error) else " (--create-folders creates it)"
            raise FolderError(f"{question.id}: {error}{hint}") from None
        question_tag = tag(folder, question.id)
        resolved.append(
            Target(question, folder, question_tag, tagged_title(question, question_tag))
        )
    return resolved


def selected(questions: list[AnyQuestion], only: list[str] | None) -> list[AnyQuestion]:
    wanted = set(only or [])
    unknown = wanted - {q.id for q in questions}
    if unknown:
        raise SpecError(f"no questions with ids {sorted(unknown)}")
    return [q for q in questions if not wanted or q.id in wanted]


def describe(question: AnyQuestion) -> str:
    match question:
        case MultipleChoice():
            correct = sum(choice.correct for choice in question.choices)
            scoring = question.effective_scoring or "single answer"
            detail = f"{len(question.choices)} choices, {correct} correct, {scoring}"
        case FillInTheBlank():
            detail = f"{len(question.blanks)} blanks" + (
                ", partial credit" if question.partial_credit else ""
            )
        case Essay():
            detail = f"limit {question.char_limit}" if question.char_limit else "no limit"
    tools = {
        "none": [],
        "scientific": ["scientific calculator"],
        "graphing": ["graphing calculator"],
        "both": ["graphing and scientific calculators"],
    }[question.calculator]
    if question.spreadsheet:
        tools.append("spreadsheet")
    if question.group:
        tools.append(f"group {question.group!r}")
    if question.case_study:
        count = len(question.case_study)
        tools.append(f"case study ({count} tab{'s' if count > 1 else ''})")
    extra = f", {', '.join(tools)}" if tools else ""
    return f"{EDITORS[question.type]} {question.points:g} pt, {detail}{extra}"


def report(found: list[str]) -> bool:
    for difference in found:
        print(f"  differs: {difference}")
    return bool(found)


def item_ids(found: list[Found]) -> str:
    return ", ".join(str(item.item_id) for item in found)


def outcome(problems: int) -> int:
    if problems:
        print(f"{problems} problem(s); see above.")
    return 1 if problems else 0


def pause(args: argparse.Namespace, prompt: str) -> None:
    if args.pause:
        input(f"  {prompt}. ")


@contextmanager
def signed_in(args: argparse.Namespace) -> Iterator[Page]:
    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            Path(args.profile), headless=False, no_viewport=True
        )
        session = Path(args.profile) / SESSION_FILE
        page = context.pages[0] if context.pages else context.new_page()
        try:
            restore_session(context, session)
            page.on("dialog", accept)
            sign_in(page, args.timeout_ms, lambda: print(LOGIN_PROMPT))
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
