from __future__ import annotations

from pathlib import Path

from examsoft_questions.models import FillInTheBlank, MultipleChoice, RangeBlank, TextBlank
from examsoft_questions.render import content_html, paragraphs, plain_text, stem_html, strip_tags
from examsoft_questions.spec import load

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"


def test_paragraphs_escape_and_break() -> None:
    assert paragraphs("a < b\nc\n\n\nd & e\n") == "<p>a &lt; b<br />c</p><p>d &amp; e</p>"


def test_stem_html_replaces_blank_markers() -> None:
    question = FillInTheBlank(
        id="f",
        type="fitb",
        folder="F",
        stem="x {{1}} y {{ 2 }}",
        blanks=[TextBlank(answers=["a"]), RangeBlank(range=(1, 2))],
    )
    assert stem_html(question) == (
        '<p>x <img alt="" src="/STW-war/resources/images/blanks/blank_1.jpg" /> y '
        '<img alt="" src="/STW-war/resources/images/blanks/blank_2.jpg" /></p>'
    )


def test_content_html_passes_html_through() -> None:
    question = load(EXAMPLE).questions[3]
    assert stem_html(question).startswith("<p>Explain why <strong>")
    mc = load(EXAMPLE).questions[0]
    assert isinstance(mc, MultipleChoice)
    assert content_html(mc.choices[0]) == "<p>Depth-first search</p>"


def test_plain_text_and_strip_tags() -> None:
    assert plain_text("<p>a&lt;b</p>\n<p>c</p>") == "a<b c"
    assert strip_tags("#<em>2</em> is bad") == "#2 is bad"
