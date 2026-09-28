"""The short hash that ties a question in ExamSoft to its spec question.

It is added to the question's title as ` [tag]`, which the portal's keyword search
finds. It depends only on the question's folder and its spec id, so editing the
question in the spec keeps its tag; moving it to another folder gives it a new one.
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING

from .folders import Folder
from .models import Question
from .render import plain_text, stem_html

if TYPE_CHECKING:
    from .portal import Item

TAG_LENGTH = 6
"""Base32 characters: about 10^9 values, and unlike any word in a stem."""
STEM_TITLE_CHARS = 20
"""Without a title, ExamSoft uses the stem's first 20 characters; so does the tagged title."""


def tag_for(folder: Folder, question_id: str) -> str:
    digest = hashlib.sha256(f"{folder.name}\n{question_id}".encode()).digest()
    return base64.b32encode(digest).decode().lower()[:TAG_LENGTH]


def tagged_title(question: Question, tag: str) -> str:
    base = question.title or plain_text(stem_html(question))[:STEM_TITLE_CHARS].rstrip()
    return f"{base} [{tag}]"


def has_tag(title: str, tag: str) -> bool:
    return f"[{tag}]" in title


def tagged_in(results: list[Item], tag: str, folder: Folder) -> list[Item]:
    """Search results that are the tag's question: the exact `[tag]` in the title (the
    search also matches stems and choices), in the folder itself (not a subfolder)."""
    return [item for item in results if has_tag(item.title, tag) and item.folder_key == folder.key]
