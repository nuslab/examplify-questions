"""The question spec: a YAML file of questions and the defaults they share."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

Calculator = Literal["none", "scientific", "graphing", "both"]
MultipleAnswerScoring = Literal["partial", "all_or_nothing", "plus_minus"]
"""ExamSoft's three multiple-answer modes, which the editor keeps mutually exclusive.

- partial: "Partial Credit"; credit per correct choice, and the exam taker may select
  only as many choices as are correct.
- all_or_nothing: "Select All That Apply"; any number may be selected, and one wrong
  or missing choice scores zero.
- plus_minus: "+/- Partial Credit"; each wrong selection subtracts a correct one.
"""
BLANK_MARKER = re.compile(r"\{\{\s*(\d+)\s*\}\}")
"""A FITB stem marks blank n (1-based, in `blanks` order) as `{{n}}`."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Content(Strict):
    """Text given as plain text or as HTML, as for a stem or an answer choice."""

    text: str | None = None
    html: str | None = None

    @model_validator(mode="after")
    def one_form(self) -> Self:
        if (self.text is None) == (self.html is None):
            raise ValueError("give exactly one of text or html")
        return self


class Choice(Content):
    correct: bool = False
    locked: bool = False
    """Keep this choice in place when choices are randomized."""


class CaseStudyTab(Content):
    """One tab of a case study artefact, shown with the question in Examplify."""

    title: Annotated[str, Field(min_length=1, max_length=30)]
    """The tab's label; the portal's tab title field takes at most 30 characters."""


class QuestionBase(Strict):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    """The spec's own key for the question; with its folder, it makes the title tag."""
    title: str | None = None
    """ExamSoft's title; it uses the first 20 characters of the stem when left out."""
    folder: Annotated[str, Field(min_length=1)]
    """Folder path, `/`-separated; a unique tail such as `CS101/Final` is enough."""
    stem: str | None = None
    stem_html: str | None = None
    points: Annotated[float, Field(gt=0)] = 1.0
    calculator: Calculator = "none"
    spreadsheet: bool = False
    group: str | None = None
    """Questions with the same group stay together on a randomized assessment."""
    cut_score: Annotated[float, Field(ge=0, le=1)] | None = None
    rationale: str | None = None
    """Shown to exam takers with their results, as ExamSoft's Rationale field."""
    case_study: Annotated[list[CaseStudyTab], Field(min_length=1, max_length=5)] | None = None
    """ExamSoft's case study artefact: up to 5 tabs of material beside the question."""

    @model_validator(mode="after")
    def one_stem(self) -> Self:
        if (self.stem is None) == (self.stem_html is None):
            raise ValueError("give exactly one of stem or stem_html")
        return self

    @field_validator("folder")
    @classmethod
    def folder_parts(cls, folder: str) -> str:
        if any(not part.strip() for part in folder.split("/")):
            raise ValueError(f"empty folder name in {folder!r}")
        return folder

    @property
    def folder_path(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.folder.split("/"))


class MultipleChoice(QuestionBase):
    type: Literal["mc"]
    choices: Annotated[list[Choice], Field(min_length=2)]
    scoring: MultipleAnswerScoring | None = None
    """How several correct choices are scored; `partial` when left out."""
    randomize_choices: bool = False

    @model_validator(mode="after")
    def correct_choices(self) -> Self:
        correct = sum(choice.correct for choice in self.choices)
        if not correct:
            raise ValueError("mark at least one choice correct")
        return self

    @property
    def effective_scoring(self) -> MultipleAnswerScoring | None:
        if self.scoring is None and sum(choice.correct for choice in self.choices) > 1:
            return "partial"
        return self.scoring


class TextBlank(Strict):
    answers: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
    """Accepted answers; ExamSoft joins them with `|`."""

    @field_validator("answers")
    @classmethod
    def no_separator(cls, answers: list[str]) -> list[str]:
        for answer in answers:
            if "|" in answer:
                raise ValueError(f"answer {answer!r} contains |, ExamSoft's answer separator")
        return answers


class RangeBlank(Strict):
    range: tuple[float, float]
    """Inclusive lower and upper limit of an accepted number; ExamSoft needs lower < upper."""

    @field_validator("range")
    @classmethod
    def ordered(cls, limits: tuple[float, float]) -> tuple[float, float]:
        if limits[0] >= limits[1]:
            raise ValueError(
                f"lower limit {limits[0]} must be below upper limit {limits[1]};"
                " for one exact number use a text blank"
            )
        return limits


def blank_kind(value: object) -> str:
    """A blank with a `range` is a numeric range; any other is a text blank."""
    has_range = "range" in value if isinstance(value, dict) else isinstance(value, RangeBlank)
    return "range" if has_range else "text"


Blank = Annotated[
    Annotated[TextBlank, Tag("text")] | Annotated[RangeBlank, Tag("range")],
    Discriminator(blank_kind),
]


class FillInTheBlank(QuestionBase):
    type: Literal["fitb"]
    blanks: Annotated[list[Blank], Field(min_length=1)]
    partial_credit: bool = False

    @model_validator(mode="after")
    def markers(self) -> Self:
        stem = self.stem if self.stem is not None else self.stem_html or ""
        found = [int(n) for n in BLANK_MARKER.findall(stem)]
        expected = list(range(1, len(self.blanks) + 1))
        if sorted(found) != expected:
            raise ValueError(
                f"the stem must mark each of blanks {expected} once as {{{{n}}}}, found {found}"
            )
        return self


class Essay(QuestionBase):
    type: Literal["essay"]
    char_limit: Annotated[int, Field(gt=0)] | None = None


Question = Annotated[MultipleChoice | FillInTheBlank | Essay, Field(discriminator="type")]


class Spec(Strict):
    questions: Annotated[list[Question], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        seen: set[str] = set()
        for question in self.questions:
            if question.id in seen:
                raise ValueError(f"duplicate question id {question.id!r}")
            seen.add(question.id)
        return self
