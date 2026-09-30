from __future__ import annotations

from pathlib import Path
from typing import Any

from examsoft_questions.compare import differences, normal_blank
from examsoft_questions.folders import Folder
from examsoft_questions.models import CaseStudyTab, FillInTheBlank, MultipleChoice
from examsoft_questions.spec import load

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"
SANDBOX = Folder(("CS101", "Sandbox"), "k8")
MC_TITLE = "EX2 multiple answer [abc234]"


def base_state(**changes: Any) -> dict[str, Any]:  # noqa: ANN401
    state: dict[str, Any] = {
        "folderKey": "k8",
        "title": MC_TITLE,
        "weight": "2.0",
        "group": "",
        "cutScore": "",
        "rationale": "",
        "charLimit": None,
        "stem": "Which of these numbers are prime? Select all that apply.",
        "stemBlanks": 0,
        "caseStudy": [],
        "options": {
            "partial": True,
            "allThatApply": False,
            "plusMinus": False,
            "randomize": True,
            "graphing": False,
            "scientific": True,
            "spreadsheet": False,
        },
        "choices": [
            {"text": "2", "correct": True, "locked": False},
            {"text": "4", "correct": False, "locked": False},
            {"text": "7", "correct": True, "locked": False},
            {"text": "9", "correct": False, "locked": False},
            {"text": "None of the above", "correct": False, "locked": True},
        ],
        "blanks": [],
    }
    return state | changes


def mc_texts() -> dict[str, Any]:
    return {
        "caseStudy": [],
        "stem": "Which of these numbers are prime? Select all that apply.",
        "choices": ["2", "4", "7", "9", "None of the above"],
    }


def test_matching_mc_has_no_differences() -> None:
    question = load(EXAMPLE).questions[1]
    assert isinstance(question, MultipleChoice)
    assert differences(question, SANDBOX, MC_TITLE, base_state(), mc_texts()) == []


def test_mc_differences_are_named() -> None:
    question = load(EXAMPLE).questions[1]
    options = base_state()["options"] | {"partial": False, "allThatApply": True}
    choices = base_state()["choices"]
    choices[1] = {"text": "4", "correct": True, "locked": False}
    state = base_state(
        folderKey="other", title="EX2", weight="1.0", options=options, choices=choices
    )
    found = differences(question, SANDBOX, MC_TITLE, state, mc_texts())
    names = [line.split(":")[0] for line in found]
    assert names == [
        "folder",
        "title",
        "points",
        "partial credit",
        "select all that apply",
        "choices",
    ]


def test_fitb_blank_rows_normalise() -> None:
    question = load(EXAMPLE).questions[2]
    assert isinstance(question, FillInTheBlank)
    state = base_state(
        title="EX3 fill in the blank [xyz567]",
        stem="The capital of France is , and pi to within 0.01 is .",
        stemBlanks=2,
        options=base_state()["options"] | {"scientific": False, "randomize": None},
        choices=[],
        blanks=[
            {"type": "BLANK", "values": ["Paris|paris|"]},
            {"type": "RANGE", "values": ["3.13", "3.15"]},
        ],
    )
    texts = {"stem": state["stem"], "choices": [], "caseStudy": []}
    assert differences(question, SANDBOX, "EX3 fill in the blank [xyz567]", state, texts) == []
    assert normal_blank({"type": "BLANK", "values": ["a||b|"]}) == ("BLANK", ("a", "b"))


def test_case_study_differences() -> None:
    question = load(EXAMPLE).questions[1]
    assert isinstance(question, MultipleChoice)
    tabs = [CaseStudyTab(title="Data", text="n = 10"), CaseStudyTab(title="Notes", html="<p>x</p>")]
    question = question.model_copy(update={"case_study": tabs})
    texts = mc_texts() | {"caseStudy": ["n = 10", "x"]}
    matching = [{"title": "Data", "text": "n = 10"}, {"title": "Notes", "text": "x"}]
    assert differences(question, SANDBOX, MC_TITLE, base_state(caseStudy=matching), texts) == []
    retitled = [{"title": "Tab 1", "text": "n = 10"}, {"title": "Notes", "text": "x"}]
    found = differences(question, SANDBOX, MC_TITLE, base_state(caseStudy=retitled), texts)
    assert [line.split(":")[0] for line in found] == ["case study"]


def test_plus_minus_ticks_select_all_that_apply() -> None:
    question = load(EXAMPLE).questions[1]
    assert isinstance(question, MultipleChoice)
    question = question.model_copy(update={"scoring": "plus_minus"})
    options = base_state()["options"] | {"partial": False, "allThatApply": True, "plusMinus": True}
    state = base_state(options=options)
    assert differences(question, SANDBOX, MC_TITLE, state, mc_texts()) == []


def test_title_entities_match_their_characters() -> None:
    question = load(EXAMPLE).questions[1]
    title = "x=1 & y [abc234]"
    state = base_state(title="x&#61;1 &amp; y [abc234]")
    assert differences(question, SANDBOX, title, state, mc_texts()) == []
