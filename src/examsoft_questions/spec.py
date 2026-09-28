"""Loading a spec file: questions with the defaults they share, validated."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from .models import DiscriminatedQuestion, Essay, FillInTheBlank, MultipleChoice, Question, Spec

QUESTION_TYPES: dict[str, type[Question]] = {
    "mc": MultipleChoice,
    "fitb": FillInTheBlank,
    "essay": Essay,
}


class SpecError(ValueError):
    """The spec file is not valid YAML or does not describe valid questions."""


def load(path: Path) -> Spec:
    """Read a spec, filling missing fields from `defaults` for the types that have them."""
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
    adapter: TypeAdapter[Question] = TypeAdapter(DiscriminatedQuestion)
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
