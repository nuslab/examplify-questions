from __future__ import annotations

import struct

from examsoft_questions.formulas import formula, png_mathml

SPEC = (
    '<math display="inline" xmlns="http://www.w3.org/1998/Math/MathML"><semantics><mrow>'
    "<msup><mi>n</mi><mo>\u2032</mo></msup>"
    "<msub><mi>n</mi><mrow><mi>c</mi><mi>h</mi><mi>a</mi><mi>r</mi></mrow></msub>"
    '<mo stretchy="false" form="prefix">(</mo><mspace width="0.222em"></mspace>'
    "<mtext> if </mtext><mi>t</mi></mrow>"
    '<annotation encoding="application/x-tex">n\'n_{char}(\\ \\text{ if }t</annotation>'
    "</semantics></math>"
)
SAVED = (
    '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">'
    "<msup><mi>n</mi><mo>'</mo></msup>"
    "<msub><mi>n</mi><mrow><mi>c</mi><mi>h</mi><mi>a</mi><mi>r</mi></mrow></msub>"
    "<mo>(</mo><mtext>if</mtext><mi>t</mi></math>"
)


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + bytes(4)


def test_spec_and_saved_mathml_read_alike() -> None:
    assert formula(SPEC) == formula(SAVED) == "msup(n,')msub(n,char)(ift"


def test_wiris_rewrites_read_alike() -> None:
    # Pandoc's MathML, and what WIRIS keeps of it in a formula image.
    script, fraktur, double, mono = (
        f'<mi mathvariant="{v}">' for v in ("script", "fraktur", "double-struck", "monospace")
    )
    pairs = [
        (f"{script}\u2112</mi>", f"{script}L</mi>"),
        (f"{fraktur}\u212d</mi>", f"{fraktur}C</mi>"),
        (f"{double}\U0001d53c</mi>", f"{double}E</mi>"),
        (f"{double}\u2115</mi>", '<mi mathvariant="normal">\u2115</mi>'),
        (f"{mono}\U0001d6a1</mi>", f"{mono}x</mi>"),
        (
            '<mover><mi>x</mi><mo accent="true">\u0302</mo></mover>',
            "<mover><mi>x</mi><mo>^</mo></mover>",
        ),
        ("<msup><mi>f</mi><mo>\u2033</mo></msup>", "<msup><mi>f</mi><mo>''</mo></msup>"),
        ("<mo>\u223c</mo><mo>\u22c3</mo>", '<mo>~</mo><mo largeop="true">\u222a</mo>'),
        ('<mstyle displaystyle="false"><mn>1</mn></mstyle>', "<mn>1</mn>"),
    ]
    for spec, saved in pairs:
        assert formula(f"<math>{spec}</math>") == formula(f"<math>{saved}</math>"), spec


def test_styles_still_differ() -> None:
    plain = formula("<math><mi>L</mi></math>")
    assert formula('<math><mi mathvariant="script">L</mi></math>') != plain
    assert formula('<math><mstyle displaystyle="true"><mn>1</mn></mstyle></math>') != "1"


def test_script_grouping_matters() -> None:
    flattened = SAVED.replace(
        "<msub><mi>n</mi><mrow><mi>c</mi><mi>h</mi><mi>a</mi><mi>r</mi></mrow></msub>",
        "<msub><mi>n</mi><mi>c</mi></msub><mi>h</mi><mi>a</mi><mi>r</mi>",
    )
    assert formula(flattened) != formula(SPEC)


def test_png_mathml() -> None:
    header = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", bytes(13)) + chunk(b"tEXt", b"Software\0W")
    assert png_mathml(header + chunk(b"tEXt", b"MathML\0" + SAVED.encode())) == SAVED
    assert png_mathml(header + chunk(b"IEND", b"")) is None
