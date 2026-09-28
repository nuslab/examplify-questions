"""The short hash that ties a question in ExamSoft to its spec question.

It is added to the question's title as ` [tag]`, which the portal's keyword search
finds. It depends only on the question's folder and its spec id, so editing the
question in the spec keeps its tag; moving it to another folder gives it a new one.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re

from .folders import Folder
from .models import Essay, FillInTheBlank, MultipleChoice
from .spec import stem_html

TAG_LENGTH = 6
"""Base32 characters: about 10^9 values, and unlike any word in a stem."""
TITLE_FALLBACK = 20
"""Without a title, ExamSoft uses the stem's first 20 characters; so does the tagged title."""


def tag(folder: Folder, question_id: str) -> str:
    digest = hashlib.sha256(f"{folder.name}\n{question_id}".encode()).digest()
    return base64.b32encode(digest).decode().lower()[:TAG_LENGTH]


def tagged_title(question: MultipleChoice | FillInTheBlank | Essay, question_tag: str) -> str:
    base = question.title or plain_text(stem_html(question))[:TITLE_FALLBACK].rstrip()
    return f"{base} [{question_tag}]"


def has_tag(title: str, question_tag: str) -> bool:
    return f"[{question_tag}]" in title


def plain_text(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())
