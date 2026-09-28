from __future__ import annotations

from pathlib import Path

import pytest

from examsoft_questions.cli import main

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"


def test_validate_lists_questions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "mc-multiple" in out and "5 choices, 2 correct, partial" in out
    assert "scientific calculator" in out
    assert "group 'search-basics'" in out
    assert "4 questions are valid." in out


def test_validate_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = tmp_path / "bad.yaml"
    spec.write_text("questions: [{id: a, type: essay}]", encoding="utf-8")
    assert main(["validate", str(spec)]) == 1
    assert "error:" in capsys.readouterr().err


def test_create_rejects_unknown_ids(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["create", str(EXAMPLE), "--only", "nope"]) == 1
    assert "no questions with ids ['nope']" in capsys.readouterr().err


def test_verify_rejects_unknown_ids(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify", str(EXAMPLE), "--only", "nope"]) == 1
    assert "no questions with ids ['nope']" in capsys.readouterr().err
