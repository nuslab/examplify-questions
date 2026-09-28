from __future__ import annotations

from examsoft_questions.portal import SPACE_BEFORE_MATH, messages, number


def test_helpers() -> None:
    assert number(2.0) == "2" and number(3.14) == "3.14"
    assert messages({"status": "EI_ERROR", "payload": [{"message": "No folder"}]}) == "No folder"
    assert messages({"status": "EI_ERROR", "messages": None, "payload": None}) == "status EI_ERROR"
    assert messages({"payload": [{"message": "Blank Range entry #<em>2</em> is bad"}]}) == (
        "Blank Range entry #2 is bad"
    )


def test_space_before_formula_is_kept() -> None:
    html = "<li><strong>State:</strong> <math><mi>r</mi></math>, and x <math>y</math></li>"
    assert SPACE_BEFORE_MATH.sub(">&nbsp;<math", html) == (
        "<li><strong>State:</strong>&nbsp;<math><mi>r</mi></math>, and x <math>y</math></li>"
    )
