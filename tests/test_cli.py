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


@pytest.mark.parametrize("command", ["create", "update", "verify"])
def test_rejects_unknown_ids(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command, str(EXAMPLE), "--only", "nope"]) == 1
    assert "no questions with ids ['nope']" in capsys.readouterr().err


@pytest.mark.parametrize("login_url", [None, "", "https://portal.example.com/school"])
def test_needs_a_login_url(
    login_url: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if login_url is None:
        monkeypatch.delenv("EXAMSOFT_LOGIN_URL", raising=False)
    else:
        monkeypatch.setenv("EXAMSOFT_LOGIN_URL", login_url)
    assert main(["folders"]) == 1
    assert "EXAMSOFT_LOGIN_URL" in capsys.readouterr().err
