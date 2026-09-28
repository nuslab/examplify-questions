"""Loading a spec file and rendering its text as the HTML ExamSoft's editors hold."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from .models import BLANK_MARKER, Content, Essay, FillInTheBlank, MultipleChoice, Question, Spec

QUESTION_TYPES: dict[str, type[MultipleChoice | FillInTheBlank | Essay]] = {
    "mc": MultipleChoice,
    "fitb": FillInTheBlank,
    "essay": Essay,
}
BLANK_IMAGE = '<img alt="" src="/STW-war/resources/images/blanks/blank_{n}.jpg" />'
"""The editor's placeholder for blank n, as inserted by its Add New Blank button."""


class SpecError(ValueError):
    """The spec file is not valid YAML or does not describe valid questions."""


def load(path: Path) -> Spec:
    """Read a spec, filling each question's missing fields from `defaults`.

    A default applies only to the question types that have that field, so one
    `defaults` block can hold MC options next to essay options.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise SpecError(f"{path}: {error}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        raise SpecError(f"{path}: expected a mapping with a `questions` list")
    unknown = set(raw) - {"defaults", "questions"}
    if unknown:
        raise SpecError(f"{path}: unknown top-level keys {sorted(unknown)}")
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise SpecError(f"{path}: `defaults` must be a mapping")
    known = set().union(*(model.model_fields for model in QUESTION_TYPES.values()))
    misspelled = set(defaults) - known - {"id", "type"}
    if misspelled:
        raise SpecError(f"{path}: unknown fields in defaults {sorted(misspelled)}")
    adapter: TypeAdapter[MultipleChoice | FillInTheBlank | Essay] = TypeAdapter(Question)
    questions = []
    problems = []
    for number, raw_question in enumerate(raw["questions"], start=1):
        try:
            questions.append(adapter.validate_python(with_defaults(raw_question, defaults)))
        except ValidationError as error:
            name = raw_question.get("id") if isinstance(raw_question, dict) else None
            label = f"question {number}" + (f" ({name})" if name else "")
            problems += [f"{label}: {problem}" for problem in readable(error)]
    if problems:
        raise SpecError(f"{path}:\n  " + "\n  ".join(problems))
    try:
        return Spec(questions=questions)
    except ValidationError as error:
        raise SpecError(f"{path}: {'; '.join(readable(error))}") from error


def readable(error: ValidationError) -> list[str]:
    """Pydantic's errors without the union's model names in their locations."""
    lines = []
    for item in error.errors():
        location = ".".join(
            str(part)
            for part in item["loc"][1:]
            if not str(part).startswith("function-") and part not in ("text", "range")
        )
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location}: {message}" if location else message)
    return lines


def with_defaults(question: object, defaults: dict[str, Any]) -> object:
    if not isinstance(question, dict):
        return question
    model = QUESTION_TYPES.get(question.get("type", ""))
    if model is None:
        return question
    applicable = {key: value for key, value in defaults.items() if key in model.model_fields}
    return applicable | question


def paragraphs(text: str) -> str:
    """Plain text as HTML: blank lines separate paragraphs, single newlines break lines."""
    blocks = [block.strip("\n") for block in text.strip().split("\n\n")]
    return "".join(
        "<p>" + "<br />".join(html.escape(line) for line in block.split("\n")) + "</p>"
        for block in blocks
        if block.strip()
    )


def content_html(content: Content) -> str:
    return content.html if content.html is not None else paragraphs(content.text or "")


def stem_html(question: MultipleChoice | FillInTheBlank | Essay) -> str:
    body = question.stem_html if question.stem_html is not None else paragraphs(question.stem or "")
    if isinstance(question, FillInTheBlank):
        return BLANK_MARKER.sub(lambda m: BLANK_IMAGE.format(n=int(m.group(1))), body)
    return body
