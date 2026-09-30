"""Where a question, as the portal's editor holds it, differs from the spec."""

from __future__ import annotations

import html
from typing import Any

from .folders import Folder
from .models import (
    CALCULATORS,
    Essay,
    FillInTheBlank,
    MultipleChoice,
    Question,
    RangeBlank,
    TextBlank,
)


def differences(
    question: Question,
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
    # The portal stores some characters of a title as entities, such as `=` as `&#61;`.
    check("title", title, html.unescape(state["title"]))
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


def blank_state(blank: TextBlank | RangeBlank) -> tuple[str, tuple[str | float, ...]]:
    if isinstance(blank, TextBlank):
        return ("BLANK", tuple(blank.answers))
    return ("RANGE", blank.range)


def normal_blank(row: dict[str, Any]) -> tuple[str, tuple[str | float, ...]]:
    """An editor blank row; saved text answers come back with a trailing `|`."""
    if row["type"] == "RANGE":
        return ("RANGE", tuple(float(value) for value in row["values"]))
    return (row["type"], tuple(a for a in "|".join(row["values"]).split("|") if a))


def as_float(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def as_int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None
