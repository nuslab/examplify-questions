"""Question folders from the portal's folder tree, and resolution of spec folder paths."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

TITLE, KEY, CHILDREN = 0, 1, 9
"""Positions in the tree endpoint's node arrays: [title, key, _, count, ..., children, ...]."""


@dataclass(frozen=True)
class Folder:
    path: tuple[str, ...]
    key: str

    @property
    def name(self) -> str:
        return "/".join(self.path)


class FolderError(LookupError):
    """A spec folder path matches no folder, or more than one."""


def folders(tree: object) -> list[Folder]:
    """Flatten the `data` of `/ei/questionstree/editablefolders`: one root node array."""
    return list(_walk(tree, ()))


def _walk(node: object, parent: tuple[str, ...]) -> Iterator[Folder]:
    if not isinstance(node, list) or len(node) <= CHILDREN:
        raise ValueError(f"unexpected folder tree node: {str(node)[:80]}")
    path = (*parent, str(node[TITLE]))
    yield Folder(path, str(node[KEY]))
    for child in node[CHILDREN] or []:
        yield from _walk(child, path)


def resolve(available: list[Folder], wanted: tuple[str, ...]) -> Folder:
    """The one folder whose path ends with `wanted`, e.g. `CS101/Final`."""
    matches = [f for f in available if f.path[-len(wanted) :] == wanted]
    if len(matches) == 1:
        return matches[0]
    name = "/".join(wanted)
    if not matches:
        raise FolderError(f"no question folder you can edit matches {name!r}")
    listed = "\n  ".join(match.name for match in matches)
    raise FolderError(f"{name!r} matches several folders; give more of the path:\n  {listed}")


def missing_tail(
    available: list[Folder], wanted: tuple[str, ...]
) -> tuple[Folder, tuple[str, ...]]:
    """The deepest existing folder of `wanted` and the names still to create below it."""
    for split in range(len(wanted) - 1, 0, -1):
        if any(f.path[-split:] == wanted[:split] for f in available):
            return resolve(available, wanted[:split]), wanted[split:]
    raise FolderError(f"no existing folder to create {'/'.join(wanted)!r} under")
