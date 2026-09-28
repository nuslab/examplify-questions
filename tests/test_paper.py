from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from examsoft_questions.paper import MINUS, PaperError, dump_spec, paper_spec, variants
from examsoft_questions.spec import load

PAPER = Path(__file__).parent / "fixtures" / "paper"
EXAMPLE = Path(__file__).parents[1] / "examples" / "paper"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return paper_spec(PAPER)


def by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {question["id"]: question for question in spec["questions"]}


def test_parts_in_natural_order_with_config(spec: dict[str, Any]) -> None:
    assert [q["id"] for q in spec["questions"]] == ["2A", "2B", "10A", "10B", "10C"]
    assert spec["defaults"] == {"folder": "CS101/Sandbox", "calculator": "none"}
    questions = by_id(spec)
    assert questions["10A"]["points"] == 1.5  # Question override beats the label.
    assert questions["10B"]["calculator"] == "scientific"  # Part override.
    assert "calculator" not in questions["2A"]


def test_multiple_choice(spec: dict[str, Any]) -> None:
    questions = by_id(spec)
    single = questions["2A"]
    assert single["type"] == "mc" and single["points"] == 1.0 and "scoring" not in single
    assert [c["correct"] for c in single["choices"]] == [True, False]
    assert single["choices"][0]["html"] == "Yes"
    assert single["title"] == "2A. [1 mark] Is the maze connected?"
    assert single["stem_html"].startswith("<p><strong>2A. [1 mark]</strong>")
    # "Select all that apply" with one correct option is still multiple answer.
    select_all = questions["2B"]
    assert select_all["scoring"] == "plus_minus"
    assert [c["correct"] for c in select_all["choices"]] == [True, False, False]
    # `a.` set apart from its bullet list keeps the list as its option text.
    assert "<li>when entered first</li>" in select_all["choices"][0]["html"]
    assert select_all["choices"][2]["html"] == "<p>None of the above</p>"
    assert questions["10C"]["scoring"] == "plus_minus"


def test_fill_in_the_blank(spec: dict[str, Any]) -> None:
    questions = by_id(spec)
    appended = questions["10A"]
    assert appended["type"] == "fitb"
    assert appended["stem_html"].endswith("<p>{{1}}</p>")
    assert appended["blanks"] == [{"answers": ["3"]}]
    table = questions["10B"]
    assert "<td>(1) {{1}}</td>" in table["stem_html"]
    assert table["blanks"] == [
        {"answers": ["Max", "max", "MAX"]},
        {"answers": [MINUS + "1", "-1"]},
    ]


def test_case_study_and_group(spec: dict[str, Any]) -> None:
    questions = by_id(spec)
    maze = questions["2A"]["case_study"]
    assert [tab["title"] for tab in maze] == ["The Maze"]
    assert '<math display="inline"' in maze[0]["html"]
    assert 'href="https://example.com/notes"' in maze[0]["html"]  # URLs stay links.
    assert questions["2B"]["case_study"] is maze  # Shared, so the YAML writes it once.
    assert questions["2A"]["group"] == "Part 2: Graphs"
    # A link to another part's file brings that part's context in as the first tab.
    games = questions["10A"]["case_study"]
    assert [tab["title"] for tab in games] == ["The Maze", "Games"]
    assert "Refer to The Maze." in games[1]["html"] and "href" not in games[1]["html"]


def test_dump_loads_as_a_valid_spec(spec: dict[str, Any], tmp_path: Path) -> None:
    text = dump_spec(spec)
    assert text.count("&id001") == 1
    out = tmp_path / "spec.yaml"
    out.write_text(text, encoding="utf-8")
    loaded = load(out)
    assert len(loaded.questions) == 5
    assert yaml.safe_load(text)["defaults"]["folder"] == "CS101/Sandbox"


def test_example_paper_is_a_valid_spec(tmp_path: Path) -> None:
    out = tmp_path / "spec.yaml"
    out.write_text(dump_spec(paper_spec(EXAMPLE)), encoding="utf-8")
    assert [q.id for q in load(out).questions] == ["1A", "1B", "1C", "1D", "2A", "2B"]


def test_variants() -> None:
    assert variants("LL") == ["LL", "ll", "Ll"]
    assert variants("0") == ["0"]


def paper_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "paper"
    shutil.copytree(PAPER, copy)
    return copy


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: (p / "examsoft.yaml").write_text("defaults: {}"), "`folder` is required"),
        (
            lambda p: (p / "examsoft.yaml").write_text("folder: F\nquestions: {9Z: {}}"),
            "'9Z'",
        ),
        (
            lambda p: (p / "solutions" / "q2-graphs.md").write_text("**2A. a**\n"),
            "2B: no `**2B. answer**` line",
        ),
        (
            lambda p: (p / "solutions" / "q2-graphs.md").write_text("**2A. d**\n\n**2B. a**\n"),
            "does not name options a..b",
        ),
        (
            lambda p: (p / "solutions" / "q10-games.md").write_text(
                "**10A. 3**\n\n**10B. (1) Max**\n\n**10C. a**\n"
            ),
            "2 blanks",
        ),
    ],
)
def test_errors(tmp_path: Path, change: Any, message: str) -> None:  # noqa: ANN401
    paper = paper_copy(tmp_path)
    change(paper)
    with pytest.raises(PaperError, match=message.replace("*", r"\*").replace("(", r"\(")):
        paper_spec(paper)


def test_too_many_case_study_tabs(tmp_path: Path) -> None:
    (tmp_path / "questions").mkdir()
    (tmp_path / "solutions").mkdir()
    (tmp_path / "examsoft.yaml").write_text("folder: F")
    links = " ".join(f"[p{n}](p{n}.md)" for n in range(2, 7))
    for n in range(1, 7):
        context = f"**Part {n}** {links if n == 1 else ''}"
        body = f"# Part {n}\n\n## Context\n\n{context}\n\n## Questions\n\n**{n}A. [1 mark]** x\n"
        (tmp_path / "questions" / f"p{n}.md").write_text(body)
        (tmp_path / "solutions" / f"p{n}.md").write_text(f"**{n}A. 1**\n")
    with pytest.raises(PaperError, match="more than 5 case study tabs"):
        paper_spec(tmp_path)
