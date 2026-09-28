from __future__ import annotations

import re
from pathlib import Path

from examsoft_questions.folders import Folder
from examsoft_questions.models import Essay, FillInTheBlank, TextBlank
from examsoft_questions.spec import load
from examsoft_questions.tags import has_tag, plain_text, tag, tagged_title

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"
SANDBOX = Folder(("ITEMS", "Faculty", "2026 Fall", "CS101", "Sandbox"), "k8")
FINAL = Folder(("ITEMS", "Faculty", "2026 Fall", "CS101", "Final"), "k9")


def test_tag_is_short_base32_and_stable() -> None:
    first = tag(SANDBOX, "q1a")
    assert re.fullmatch(r"[a-z2-7]{6}", first)
    assert tag(SANDBOX, "q1a") == first
    assert tag(Folder(SANDBOX.path, "another-key"), "q1a") == first  # The path counts, not the key.


def test_tag_depends_on_folder_and_id() -> None:
    tags = {tag(SANDBOX, "q1a"), tag(SANDBOX, "q1b"), tag(FINAL, "q1a")}
    assert len(tags) == 3


def test_tag_ignores_question_content() -> None:
    spec = load(EXAMPLE)
    edited = spec.questions[0].model_copy(update={"points": 7.0, "title": "Renamed"})
    assert tag(SANDBOX, edited.id) == tag(SANDBOX, spec.questions[0].id)


def test_tagged_title_uses_title_or_stem_start() -> None:
    titled = load(EXAMPLE).questions[0]
    assert tagged_title(titled, "abc234") == "EX1 single answer [abc234]"
    essay = Essay(
        id="e",
        type="essay",
        folder="F",
        stem_html="<p>Explain <b>why</b> A* &amp; IDA* differ.</p>",
    )
    assert tagged_title(essay, "abc234") == "Explain why A* & IDA [abc234]"
    fitb = FillInTheBlank(
        id="f",
        type="fitb",
        folder="F",
        stem="{{1}} is the capital",
        blanks=[TextBlank(answers=["P"])],
    )
    assert tagged_title(fitb, "abc234") == "is the capital [abc234]"


def test_has_tag_needs_the_brackets() -> None:
    assert has_tag("1A. Primes [abc234]", "abc234")
    assert not has_tag("abc234 appears in the stem", "abc234")
    assert plain_text("<p>a&lt;b</p>\n<p>c</p>") == "a<b c"
