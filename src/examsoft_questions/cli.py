"""Create ExamSoft questions from a YAML spec."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from .folders import Folder, FolderError
from .models import Essay, FillInTheBlank, MultipleChoice, Question
from .paper import PaperError, dump_spec, paper_spec
from .portal import Item, Portal, PortalError, SaveStatus
from .session import signed_in
from .spec import SpecError, load
from .tags import tag_for, tagged_title

TYPE_LABELS = {"mc": "multiple choice", "fitb": "fill in the blank", "essay": "essay"}
CALCULATOR_LABELS: dict[str, list[str]] = {
    "none": [],
    "scientific": ["scientific calculator"],
    "graphing": ["graphing calculator"],
    "both": ["graphing and scientific calculators"],
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (SpecError, FolderError, PortalError, PaperError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    browser = argparse.ArgumentParser(add_help=False)
    browser.add_argument("--profile", default="../data/examsoft-profile")
    browser.add_argument(
        "--timeout",
        dest="timeout_ms",
        type=lambda seconds: int(seconds) * 1000,
        default=60_000,
        metavar="SECONDS",
        help="seconds per page action (default: 60)",
    )
    spec = argparse.ArgumentParser(add_help=False)
    spec.add_argument("spec", type=Path)
    spec.add_argument("--only", nargs="+", metavar="ID", help="only these question ids")
    editing = argparse.ArgumentParser(add_help=False)
    editing.add_argument(
        "--dry-run",
        action="store_true",
        help="fill the editors and compare them with the spec, but never save",
    )
    editing.add_argument(
        "--pause", action="store_true", help="wait for Enter before saving or leaving each editor"
    )
    editing.add_argument("--approve", action="store_true", help="approve instead of saving a draft")

    root = argparse.ArgumentParser(prog="examsoft-questions", description=__doc__.split("\n")[0])
    commands = root.add_subparsers(required=True)

    import_md = commands.add_parser(
        "import-md", help="convert a paper in pandoc Markdown into a spec (offline)"
    )
    import_md.add_argument("paper", type=Path, help="directory with questions/ and solutions/")
    import_md.add_argument(
        "--config", type=Path, help="ExamSoft settings (default: PAPER/examsoft.yaml)"
    )
    import_md.add_argument("-o", "--output", type=Path, help="write the spec here, not to stdout")
    import_md.set_defaults(handler=cmd_import_md)

    validate = commands.add_parser("validate", help="check a spec without signing in")
    validate.add_argument("spec", type=Path)
    validate.set_defaults(handler=cmd_validate)

    listing = commands.add_parser(
        "folders", parents=[browser], help="list the question folders you can add to"
    )
    listing.add_argument("--match", default="", help="only folders whose path contains this")
    listing.set_defaults(handler=cmd_folders)

    create = commands.add_parser(
        "create", parents=[spec, editing, browser], help="create the spec's questions in ExamSoft"
    )
    create.add_argument(
        "--create-folders", action="store_true", help="create missing folders at the path's end"
    )
    create.set_defaults(handler=cmd_create)

    update_help = "bring the spec's questions in ExamSoft up to date with the spec"
    commands.add_parser(
        "update",
        parents=[spec, editing, browser],
        help=update_help,
        description=f"{update_help}; approved questions are always approved again",
    ).set_defaults(handler=cmd_update)

    commands.add_parser(
        "verify",
        parents=[spec, browser],
        help="find the spec's questions in ExamSoft and compare them with the spec",
    ).set_defaults(handler=cmd_verify)
    return root


def cmd_import_md(args: argparse.Namespace) -> int:
    text = dump_spec(paper_spec(args.paper, args.config))
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
    with signed_in(Path(args.profile), args.timeout_ms) as page:
        for folder in Portal(page, args.timeout_ms).folders():
            if args.match.lower() in folder.name.lower():
                print(folder.name)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    status: SaveStatus = "Approved" if args.approve else "Draft"
    problems = 0
    with signed_in(Path(args.profile), args.timeout_ms) as page:
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
                problems += print_differences(portal.editor_differences(question, folder, title))
                pause(args, "Enter leaves this editor without saving")
                continue
            pause(args, f"Enter presses {'Approve' if args.approve else 'Save'}")
            saved = portal.save(question, status)
            print(f"  created item {saved.item_id} rev {saved.revision} ({status}): {saved.url}")
            problems += print_differences(portal.read_back(question, folder, title, saved.url))
    return exit_code(problems)


def cmd_verify(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    problems = 0
    with signed_in(Path(args.profile), args.timeout_ms) as page:
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
            for item in existing:
                if not open_locked(portal, target, item):
                    problems += 1
                    continue
                differences = portal.read_back(
                    target.question, target.folder, target.title, item.url
                )
                state = "Approved" if item.approved else "Draft"
                verdict = "" if differences else ", matches the spec"
                print(f"{target.label}: item {item.item_id} rev {item.revision} ({state}){verdict}")
                problems += print_differences(differences)
    return exit_code(problems)


def cmd_update(args: argparse.Namespace) -> int:
    questions = selected(load(args.spec).questions, args.only)
    problems = 0
    with signed_in(Path(args.profile), args.timeout_ms) as page:
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
            if not open_locked(portal, target, item):
                problems += 1
                continue
            before = portal.read_back(question, folder, title, item.url)
            if not before:
                print(f"{target.label}: item {item.item_id} is up to date")
                continue
            print(f"{target.label}: item {item.item_id} differs:")
            print_differences(before)
            portal.fill(question, folder, title, item)
            if args.dry_run:
                problems += print_differences(portal.editor_differences(question, folder, title))
                pause(args, "Enter leaves this editor without saving")
                continue
            status: SaveStatus = "Approved" if args.approve or item.approved else "Draft"
            pause(args, f"Enter presses {'Approve' if status == 'Approved' else 'Save'}")
            saved = portal.save(question, status, item)
            revision = "new revision" if saved.revision != item.revision else "same revision"
            print(f"  updated item {saved.item_id} rev {saved.revision}, {revision} ({status})")
            problems += print_differences(portal.read_back(question, folder, title, saved.url))
    return exit_code(problems)


@dataclass(frozen=True)
class Target:
    question: Question
    folder: Folder
    tag: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.question.id} [{self.tag}]"


def targets(portal: Portal, questions: list[Question], create: bool = False) -> list[Target]:
    """Resolve every folder before any question is touched, so a bad path changes nothing."""
    resolved = []
    for question in questions:
        try:
            folder = portal.folder(question.folder_path, create=create)
        except FolderError as error:
            hint = "" if create or "several" in str(error) else " (--create-folders creates it)"
            raise FolderError(f"{question.id}: {error}{hint}") from None
        tag = tag_for(folder, question.id)
        resolved.append(Target(question, folder, tag, tagged_title(question, tag)))
    return resolved


def selected(questions: list[Question], only: list[str] | None) -> list[Question]:
    wanted = set(only or [])
    unknown = wanted - {q.id for q in questions}
    if unknown:
        raise SpecError(f"no questions with ids {sorted(unknown)}")
    return [q for q in questions if not wanted or q.id in wanted]


def describe(question: Question) -> str:
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
    tools = list(CALCULATOR_LABELS[question.calculator])
    if question.spreadsheet:
        tools.append("spreadsheet")
    if question.group:
        tools.append(f"group {question.group!r}")
    if question.case_study:
        count = len(question.case_study)
        tools.append(f"case study ({count} tab{'s' if count > 1 else ''})")
    extra = f", {', '.join(tools)}" if tools else ""
    return f"{TYPE_LABELS[question.type]}, {question.points:g} pt, {detail}{extra}"


def open_locked(portal: Portal, target: Target, item: Item) -> bool:
    """Lock and open the item's editor, unless another session has it open."""
    if not portal.lock(item.item_id, item.revision):
        print(f"{target.label}: item {item.item_id} is open in another session; skipped")
        return False
    portal.open(item.url)
    return True


def print_differences(differences: list[str]) -> bool:
    for difference in differences:
        print(f"  differs: {difference}")
    return bool(differences)


def item_ids(items: list[Item]) -> str:
    return ", ".join(str(item.item_id) for item in items)


def exit_code(problems: int) -> int:
    if problems:
        print(f"{problems} problem(s); see above.")
    return 1 if problems else 0


def pause(args: argparse.Namespace, prompt: str) -> None:
    if args.pause:
        input(f"  {prompt}. ")
