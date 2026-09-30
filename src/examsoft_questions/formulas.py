"""Formulas as comparable text, from the spec's MathML and from saved formula images.

The portal keeps a formula only as a WIRIS image, whose PNG holds the MathML it was made
from, rewritten; both reduce to the same text, e.g. `msub(n,3)`. WIRIS drops `semantics`
and inline `displaystyle="false"`, regroups rows, spells a styled letter as an ASCII
letter with a `mathvariant`, and replaces some characters with ones that look alike.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from xml.etree import ElementTree

MARKER = re.compile(r"⟦([^⟧]*)⟧")
"""A formula image in an editor's text, as page.js writes it: ⟦source⟧."""
IGNORED = {"annotation", "mspace"}
GROUPS = {"math", "semantics", "mrow", "mtd"}
TOKENS = {"mi", "mn", "mo", "mtext"}
LOOKALIKES = str.maketrans(
    {
        "\u2032": "'",  # prime
        "\u2033": "''",
        "\u2034": "'''",
        "\u0302": "^",  # combining accents
        "\u0303": "~",
        "\u0307": "\u02d9",
        "\u0308": "\u00a8",
        "\u030c": "\u02c7",
        "\u223c": "~",  # tilde operator
        "\u22c3": "\u222a",  # n-ary union and intersection
        "\u22c2": "\u2229",
    }
)
"""Characters to the ones WIRIS writes for them."""
LETTERLIKE = {"FRAKTUR": "BLACK-LETTER"}
"""Styles whose letters partly sit among the Letterlike Symbols, named differently."""


def formula(mathml: str) -> str:
    return linear(ElementTree.fromstring(mathml))


def linear(element: ElementTree.Element) -> str:
    name = element.tag.rpartition("}")[2]
    if name in IGNORED:
        return ""
    if name in TOKENS:
        text = "".join("".join(element.itertext()).split())
        return styled(text, element.get("mathvariant")).translate(LOOKALIKES)
    parts = [linear(child) for child in element]
    group = name in GROUPS or (name == "mstyle" and element.get("displaystyle") == "false")
    return "".join(parts) if group else f"{name}({','.join(parts)})"


def styled(text: str, variant: str | None) -> str:
    """An ASCII letter in a `mathvariant` other than normal or italic, as its styled letter."""
    if not (len(text) == 1 and text.isascii() and text.isalpha()):
        return text
    if variant in (None, "normal", "italic"):
        return text
    style = variant.upper().replace("-", " ").replace("DOUBLE STRUCK", "DOUBLE-STRUCK")
    letter = f"{'CAPITAL' if text.isupper() else 'SMALL'} {text.upper()}"
    for name in (f"MATHEMATICAL {style} {letter}", f"{LETTERLIKE.get(style, style)} {letter}"):
        try:
            return unicodedata.lookup(name)
        except KeyError:
            continue
    return text


def png_mathml(png: bytes) -> str | None:
    """The MathML in a formula image's `MathML` text chunk."""
    offset = 8  # The PNG signature.
    while offset + 8 <= len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        kind, data = png[offset + 4 : offset + 8], png[offset + 8 : offset + 8 + length]
        if kind == b"tEXt" and data.startswith(b"MathML\0"):
            return data.removeprefix(b"MathML\0").decode("latin-1")
        offset += 12 + length
    return None
