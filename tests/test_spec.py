from __future__ import annotations

import re
from pathlib import Path

import pytest

from examsoft_questions.models import Essay, FillInTheBlank, MultipleChoice, RangeBlank, TextBlank
from examsoft_questions.spec import SpecError, load

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_loads() -> None:
    spec = load(EXAMPLE)
    kinds = [type(q) for q in spec.questions]
    assert kinds == [MultipleChoice, MultipleChoice, FillInTheBlank, Essay]
    single, multiple, fitb, essay = spec.questions
    assert isinstance(single, MultipleChoice) and single.effective_scoring is None
    assert isinstance(multiple, MultipleChoice) and multiple.effective_scoring == "partial"
    assert isinstance(fitb, FillInTheBlank)
    assert fitb.blanks == [TextBlank(answers=["Paris", "paris"]), RangeBlank(range=(3.13, 3.15))]
    assert essay.calculator == "both"
    assert essay.case_study is not None
    assert [tab.title for tab in essay.case_study] == ["Scenario", "Heuristic"]
    assert all(q.folder_path == ("2026 Fall", "CS101", "Sandbox") for q in spec.questions)


def test_defaults_apply_only_to_types_with_the_field(tmp_path: Path) -> None:
    spec = load(
        write(
            tmp_path,
            """
defaults: {folder: A/B, randomize_choices: true, char_limit: 100, points: 3}
questions:
  - {id: m, type: mc, stem: s, choices: [{text: a, correct: true}, {text: b}]}
  - {id: e, type: essay, stem: s, points: 5}
""",
        )
    )
    mc, essay = spec.questions
    assert isinstance(mc, MultipleChoice) and mc.randomize_choices and mc.points == 3
    assert isinstance(essay, Essay) and essay.char_limit == 100 and essay.points == 5


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("questions: []", "at least 1"),
        ("defaults: {colour: red}\nquestions: [{}]", "unknown fields in defaults"),
        (
            "questions: [{id: a, type: mc, folder: F, stem: s, choices: [{text: x}, {text: y}]}]",
            "at least one choice correct",
        ),
        ("questions: [{id: a, type: essay, folder: F}]", "exactly one of stem or stem_html"),
        (
            "questions: [{id: a, type: essay, folder: F, stem: s},"
            " {id: a, type: essay, folder: F, stem: t}]",
            "duplicate question id",
        ),
        (
            "questions: [{id: a, type: fitb, folder: F, stem: 'x {{1}}',"
            " blanks: [{answers: [p]}, {answers: [q]}]}]",
            "mark each of blanks [1, 2]",
        ),
        (
            "questions: [{id: a, type: fitb, folder: F, stem: 'x {{1}}',"
            " blanks: [{range: [2, 1]}]}]",
            "must be below upper limit",
        ),
        (
            "questions: [{id: a, type: fitb, folder: F, stem: 'x {{1}}',"
            " blanks: [{answers: [a|b]}]}]",
            "separator",
        ),
        ("questions: [{id: a, type: essay, folder: A//B, stem: s}]", "empty folder name"),
        ("questions: [{id: a, type: tf, folder: F, stem: s}]", "tf"),
        ("questions: [{id: a, type: essay, folder: F, stem: s, calculator: yes}]", "calculator"),
        ("nope: 1", "questions"),
        (
            "questions: [{id: a, type: essay, folder: F, stem: s, case_study: []}]",
            "at least 1 item",
        ),
        (
            "questions: [{id: a, type: essay, folder: F, stem: s,"
            " case_study: [{title: " + "x" * 31 + ", text: t}]}]",
            "at most 30 characters",
        ),
        (
            "questions: [{id: a, type: essay, folder: F, stem: s, case_study: [{title: T}]}]",
            "exactly one of text or html",
        ),
    ],
)
def test_invalid_specs(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(SpecError, match=re.escape(message)):
        load(write(tmp_path, text))
