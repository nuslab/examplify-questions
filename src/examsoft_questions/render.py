"""Spec text as the HTML ExamSoft's editors hold, and HTML back as plain text."""

from __future__ import annotations

import html
import re

from .models import BLANK_MARKER, Content, FillInTheBlank, Question

BLANK_IMAGE = '<img alt="" src="/STW-war/resources/images/blanks/blank_{n}.jpg" />'
"""The editor's placeholder for blank n, as inserted by its Add New Blank button."""
TAG = re.compile(r"<[^>]+>")


def paragraphs(text: str) -> str:
    """Plain text as HTML: blank lines separate paragraphs, single newlines break lines."""
    blocks = [block.strip("\n") for block in text.strip().split("\n\n")]
    return "".join(
        "<p>" + "<br />".join(html.escape(line) for line in block.split("\n")) + "</p>"
        for block in blocks
        if block.strip()
    )


def content_html(content: Content) -> str:
    return content.html if content.html is not None else paragraphs(content.text or "")


def stem_html(question: Question) -> str:
    body = question.stem_html if question.stem_html is not None else paragraphs(question.stem or "")
    if isinstance(question, FillInTheBlank):
        return BLANK_MARKER.sub(lambda m: BLANK_IMAGE.format(n=int(m.group(1))), body)
    return body


def strip_tags(markup: str) -> str:
    return TAG.sub("", markup)


def plain_text(markup: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", markup)).split())
