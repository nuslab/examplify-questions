"""Create ExamSoft questions from a YAML spec.

`import-md` converts a paper written in pandoc Markdown into a spec. `validate`
checks a spec offline. `folders` lists the question folders the account
can add to. `create` signs in through SSO, fills each question's editor in the
portal and saves it as a draft (or approves it with --approve), then reads it back
and reports where it differs from the spec. Each title ends with a tag hashed from
the question's folder and spec id; `create` skips questions whose tag ExamSoft
already has, and `verify` finds them by it and compares them with the spec.
`update` refills and saves the questions that differ from the spec; an approved
one gets a new revision and is approved again.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from playwright.sync_api import BrowserContext, Dialog, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .examsoft import (
    EDITORS,
    SESSION_FILE,
    AnyQuestion,
    Portal,
    PortalError,
    Saved,
    Status,
    restore_session,
    run,
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
    spec = load(args.spec)
    status: Status = "Approved" if args.approve else "Draft"
    todo = selected(spec.questions, args.only)
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        targets = {q.id: target_folder(portal, q, args) for q in todo}
        for question in todo:
            folder = targets[question.id]
            question_tag = tag(folder, question.id)
            title = tagged_title(question, question_tag)
            existing = portal.find_tagged(question_tag, folder)
            if existing:
                items = ", ".join(str(found.item_id) for found in existing)
                print(f"{question.id} [{question_tag}]: exists as item {items}; skipped")
                continue
            print(f"{question.id} [{question_tag}]: {describe(question)} -> {folder.name}")
            portal.fill(question, folder, title)
            if args.dry_run:
                found = portal.differences(question, folder, title, run(page, "state"))
                report(found)
                problems += bool(found)
                pause(args, "Enter leaves this editor without saving")
                continue
            pause(args, f"Enter presses {'Approve' if args.approve else 'Save'}")
            saved = portal.save(question, status, folder)
            print(f"  created item {saved.item_id} rev {saved.revision} ({status}): {saved.url}")
            found = portal.read_back(question, folder, title, saved)
            report(found)
            problems += bool(found)
    if problems:
        print(f"{problems} question(s) differ from the spec; check them in the portal.")
    return 1 if problems else 0


def cmd_verify(args: argparse.Namespace) -> int:
    spec = load(args.spec)
    questions = selected(spec.questions, args.only)
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        for question in questions:
            folder = target_folder(portal, question, args)
            question_tag = tag(folder, question.id)
            title = tagged_title(question, question_tag)
            existing = portal.find_tagged(question_tag, folder)
            label = f"{question.id} [{question_tag}]"
            if not existing:
                print(f"{label}: not in ExamSoft")
                problems += 1
                continue
            if len(existing) > 1:
                items = ", ".join(str(found.item_id) for found in existing)
                print(f"{label}: several items carry this tag: {items}")
                problems += 1
            for found in existing:
                if not portal.lock(found.item_id, found.revision):
                    print(f"{label}: item {found.item_id} is open in another session; skipped")
                    problems += 1
                    continue
                portal.open(found.url)
                saved = Saved(found.item_id, found.revision, found.url)
                differences = portal.read_back(question, folder, title, saved)
                state = "Approved" if found.approved else "Draft"
                verdict = "" if differences else ", matches the spec"
                print(f"{label}: item {found.item_id} rev {found.revision} ({state}){verdict}")
                report(differences)
                problems += bool(differences)
    if problems:
        print(f"{problems} problem(s); see above.")
    return 1 if problems else 0


def cmd_update(args: argparse.Namespace) -> int:
    spec = load(args.spec)
    questions = selected(spec.questions, args.only)
    status: Status
    problems = 0
    with signed_in(args) as page:
        portal = Portal(page, args.timeout_ms)
        for question in questions:
            folder = target_folder(portal, question, args)
            question_tag = tag(folder, question.id)
            title = tagged_title(question, question_tag)
            existing = portal.find_tagged(question_tag, folder)
            label = f"{question.id} [{question_tag}]"
            if len(existing) != 1:
                items = ", ".join(str(item.item_id) for item in existing)
                reason = f"several items carry this tag: {items}" if items else "not in ExamSoft"
                print(f"{label}: {reason}; skipped")
                problems += 1
                continue
            item = existing[0]
            if not portal.lock(item.item_id, item.revision):
                print(f"{label}: item {item.item_id} is open in another session; skipped")
                problems += 1
                continue
            saved = Saved(item.item_id, item.revision, item.url)
            portal.open(item.url)
            before = portal.read_back(question, folder, title, saved)
            if not before:
                print(f"{label}: item {item.item_id} is up to date")
                continue
            print(f"{label}: item {item.item_id} differs:")
            report(before)
            portal.fill(question, folder, title, item)
            if args.dry_run:
                found = portal.differences(question, folder, title, run(page, "state"))
                report(found)
                problems += bool(found)
                pause(args, "Enter leaves this editor without saving")
                continue
            # Saving an approved question makes a new revision, which assessments can use
            # only once approved; so it is approved again.
            status = "Approved" if args.approve or item.approved else "Draft"
            pause(args, f"Enter presses {'Approve' if status == 'Approved' else 'Save'}")
            saved = portal.save(question, status, folder, item)
            revision = "new revision" if saved.revision != item.revision else "same revision"
            print(f"  updated item {saved.item_id} rev {saved.revision}, {revision} ({status})")
            found = portal.read_back(question, folder, title, saved)
            report(found)
            problems += bool(found)
    if problems:
        print(f"{problems} problem(s); see above.")
    return 1 if problems else 0


def selected(questions: list[AnyQuestion], only: list[str] | None) -> list[AnyQuestion]:
    wanted = set(only or [])
    unknown = wanted - {q.id for q in questions}
    if unknown:
        raise SpecError(f"no questions with ids {sorted(unknown)}")
    return [q for q in questions if not wanted or q.id in wanted]


def target_folder(
    portal: Portal, question: MultipleChoice | FillInTheBlank | Essay, args: argparse.Namespace
) -> Folder:
    create = getattr(args, "create_folders", False) and not getattr(args, "dry_run", False)
    try:
        return portal.folder(question.folder_path, create=create)
    except FolderError as error:
        hint = "" if create or "several" in str(error) else " (--create-folders creates it)"
        raise FolderError(f"{question.id}: {error}{hint}") from None


def describe(question: MultipleChoice | FillInTheBlank | Essay) -> str:
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
    tools += ["spreadsheet"] if question.spreadsheet else []
    if question.group:
        tools.append(f"group {question.group!r}")
    if question.case_study:
        count = len(question.case_study)
        tools.append(f"case study ({count} tab{'s' if count > 1 else ''})")
    extra = f", {', '.join(tools)}" if tools else ""
    return f"{EDITORS[question.type]} {question.points:g} pt, {detail}{extra}"


def report(found: list[str]) -> None:
    for difference in found:
        print(f"  differs: {difference}")


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
        try:
            restore_session(context, session)
            page = context.pages[0] if context.pages else context.new_page()
            page.on("dialog", accept)
            sign_in(page, args.timeout_ms, lambda: print(LOGIN_PROMPT))
            save_session(context, session)
            yield page
        finally:
            with suppress(PlaywrightError):
                Portal(page).clear_locks()  # Editors opened here would stay locked otherwise.
                save_session(context, session)
            context.close()


def accept(dialog: Dialog) -> None:
    """Leaving an editor may raise its unsaved-changes prompt; every other dialog is shown."""
    if dialog.type == "beforeunload":
        with suppress(PlaywrightError):  # Already handled by the page.
            dialog.accept()
    else:
        print(f"  portal dialog: {dialog.message}")
        dialog.accept()
