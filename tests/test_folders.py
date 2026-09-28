from __future__ import annotations

import pytest

from examsoft_questions.folders import Folder, FolderError, flatten_tree, missing_tail, resolve


def node(title: str, key: str, *children: list[object]) -> list[object]:
    """A node array as the tree endpoint sends it: 13 fields, children at index 9."""
    return [title, key, None, 0, 0, None, 1, None, 1150, list(children), 0, "uuid", False]


TREE = node(
    "ITEMS",
    "k0",
    node(
        "School",
        "k1",
        node("AY1", "k2", node("CS101", "k3", node("Final", "k4"))),
        node("AY2", "k5", node("CS101", "k6", node("Final", "k7"), node("Sandbox", "k8"))),
    ),
)
ALL = flatten_tree(TREE)


def test_flatten() -> None:
    assert ALL[0] == Folder(("ITEMS",), "k0")
    assert Folder(("ITEMS", "School", "AY2", "CS101", "Sandbox"), "k8") in ALL
    assert len(ALL) == 9


def test_ends_with() -> None:
    folder = Folder(("ITEMS", "AY1", "CS101"), "k")
    assert folder.ends_with(("AY1", "CS101")) and folder.ends_with(("ITEMS", "AY1", "CS101"))
    assert not folder.ends_with(("CS2109",)) and not folder.ends_with(
        ("X", "ITEMS", "AY1", "CS101")
    )


def test_resolve_by_unique_tail() -> None:
    assert resolve(ALL, ("Sandbox",)).key == "k8"
    assert resolve(ALL, ("AY1", "CS101", "Final")).key == "k4"
    assert resolve(ALL, ("ITEMS", "School", "AY2", "CS101")).key == "k6"


def test_resolve_ambiguous_lists_matches() -> None:
    with pytest.raises(FolderError, match="several") as error:
        resolve(ALL, ("CS101", "Final"))
    assert "ITEMS/School/AY1/CS101/Final" in str(error.value)


def test_resolve_missing() -> None:
    with pytest.raises(FolderError, match="no question folder"):
        resolve(ALL, ("Nope",))
    # A tail must match whole names, not a substring.
    with pytest.raises(FolderError):
        resolve(ALL, ("box",))


def test_missing_tail() -> None:
    parent, names = missing_tail(ALL, ("AY2", "CS101", "Quiz", "Week 1"))
    assert parent.key == "k6" and names == ("Quiz", "Week 1")
    with pytest.raises(FolderError, match="several"):
        missing_tail(ALL, ("CS101", "Quiz"))
    with pytest.raises(FolderError, match="no existing folder"):
        missing_tail(ALL, ("Nowhere", "Quiz"))


def test_bad_node() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        flatten_tree(["only", "three", None])
