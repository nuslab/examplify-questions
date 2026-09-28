from __future__ import annotations

import pytest

from examsoft_questions.portal import SPACE_BEFORE_MATH, Site, messages, number


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


def test_site_from_login_url() -> None:
    site = Site.from_login_url("https://examsoft.example.com/GKWeb/login/MySchool")
    assert site == Site("https://examsoft.example.com", "myschool")
    assert site.login == "https://examsoft.example.com/GKWeb/login/myschool"
    assert site.questions == "https://examsoft.example.com/STW-war/ei/questions/s=myschool"
    assert Site.from_login_url("https://ei.examsoft.com/GKWeb/login/law/").school == "law"


@pytest.mark.parametrize(
    "url",
    [
        "http://examsoft.example.com/GKWeb/login/myschool",
        "https://examsoft.example.com/GKWeb/login/",
        "https://examsoft.example.com/STW-war/ei/questions/s=myschool",
        "examsoft.example.com/GKWeb/login/myschool",
    ],
)
def test_site_rejects_other_urls(url: str) -> None:
    with pytest.raises(ValueError, match="not an ExamSoft login page"):
        Site.from_login_url(url)
