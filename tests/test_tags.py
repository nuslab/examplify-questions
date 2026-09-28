from __future__ import annotations

import re
from pathlib import Path

from examsoft_questions.folders import Folder
from examsoft_questions.models import Essay, FillInTheBlank, TextBlank
from examsoft_questions.portal import Item
from examsoft_questions.spec import load
from examsoft_questions.tags import has_tag, tag_for, tagged_in, tagged_title

EXAMPLE = Path(__file__).parents[1] / "examples" / "sandbox.yaml"
SANDBOX = Folder(("ITEMS", "Faculty", "2026 Fall", "CS101", "Sandbox"), "k8")
FINAL = Folder(("ITEMS", "Faculty", "2026 Fall", "CS101", "Final"), "k9")


def test_tag_is_short_base32_and_stable() -> None:
    first = tag_for(SANDBOX, "q1a")
    assert re.fullmatch(r"[a-z2-7]{6}", first)
    assert tag_for(SANDBOX, "q1a") == first
    assert (
        tag_for(Folder(SANDBOX.path, "another-key"), "q1a") == first
    )  # The path counts, not the key.


def test_tag_depends_on_folder_and_id() -> None:
    tags = {tag_for(SANDBOX, "q1a"), tag_for(SANDBOX, "q1b"), tag_for(FINAL, "q1a")}
    assert len(tags) == 3


def test_tag_ignores_question_content() -> None:
    spec = load(EXAMPLE)
    edited = spec.questions[0].model_copy(update={"points": 7.0, "title": "Renamed"})
    assert tag_for(SANDBOX, edited.id) == tag_for(SANDBOX, spec.questions[0].id)


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


def test_tagged_in_keeps_exact_tag_in_the_folder() -> None:
    def item(item_id: int, title: str, folder_key: str) -> Item:
        return Item(item_id, 1, title, folder_key, False, f"/edit/{item_id}")

    results = [
        item(1, "1A. [1 mark] Is there a map [abc234]", SANDBOX.key),
        item(2, "Stem mentions abc234 without brackets", SANDBOX.key),
        item(3, "1A. [1 mark] copy in another folder [abc234]", "other"),
        item(4, "1A. [1 mark] copy in a subfolder [abc234]", "sub-of-k8"),
    ]
    assert [f.item_id for f in tagged_in(results, "abc234", SANDBOX)] == [1]
