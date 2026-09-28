"""Convert a paper written in pandoc Markdown into a question spec.

A paper directory holds `questions/*.md`, one file per part, the answers in
`solutions/*.md` under the same names, and `examsoft.yaml` for what the Markdown
does not say (see the README).
"""

from __future__ import annotations

import io
import itertools
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import panflute as pf
import yaml

from .models import BLANK_MARKER, CASE_STUDY_TABS_MAX, TAB_TITLE_MAX

READER = "markdown+tex_math_single_backslash"
LABEL = re.compile(r"^(\d+[A-Za-z])\.\s*\[(\d+(?:\.\d+)?)\s*marks?\]$")
ANSWER = re.compile(r"^(\d+[A-Za-z])\.\s+(.+)$", re.S)
NUMBERED_ANSWER = re.compile(r"\((\d+)\)\s*(.+?)\s*(?=,\s*\(\d+\)|$)", re.S)
BLANK_RUN = re.compile(r"_{3,}")
SELECT_ALL = re.compile(r"select\s+all\s+that\s+apply", re.I)
EXTERNAL = re.compile(r"^[a-zA-Z][\w+.-]*:|^#")
"""A URL or an anchor, as opposed to a link to another file of the paper."""
PART_PREFIX = re.compile(r"^Part\s+\w+\s*:\s*", re.I)
MINUS = "\u2212"
TITLE_LIMIT = 60
CONFIG_KEYS = {"folder", "defaults", "parts", "questions"}


class PaperError(ValueError):
    """The paper or its config does not have the layout this converter reads."""


# Pandoc


def pandoc_path() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    try:
        import pypandoc  # noqa: PLC0415 - optional

        return str(pypandoc.get_pandoc_path())
    except (ImportError, OSError) as error:
        raise PaperError("pandoc is needed: install it, or pip install pypandoc_binary") from error


def pandoc(args: list[str], stdin: str) -> str:
    result = subprocess.run(
        [pandoc_path(), *args], input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise PaperError(f"pandoc {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def read_doc(path: Path) -> pf.Doc:
    return pf.load(io.StringIO(pandoc(["-f", READER, "-t", "json"], path.read_text("utf-8"))))


def to_html(blocks: list[pf.Block], api: list[int]) -> str:
    """Blocks to HTML with MathML formulas; `api` is the pandoc API version they were read in."""
    blocks_json = [block.to_json() for block in blocks]
    document = {"pandoc-api-version": api, "meta": {}, "blocks": blocks_json}
    args = ["-f", "json", "-t", "html", "--mathml", "--wrap=none"]
    return pandoc(args, json.dumps(document)).strip()


# Inline text


def plain(element: pf.Element) -> str:
    """Text as a reader sees it, with formulas as their TeX source."""
    return " ".join(pf.stringify(element).split())


def bold_text(block: pf.Block) -> str | None:
    """The text of a paragraph that is all bold, as `**1A. d**`."""
    if isinstance(block, pf.Para) and len(block.content) == 1:
        (only,) = block.content
        if isinstance(only, pf.Strong):
            return plain(only)
    return None


def unlink(blocks: list[pf.Block]) -> list[str]:
    """Replace each local link, which cannot resolve in ExamSoft, with its text.

    Returns the links' targets.
    """
    targets: list[str] = []

    def action(element: pf.Element, _: pf.Doc) -> list[pf.Inline] | None:
        if isinstance(element, pf.Link) and not EXTERNAL.match(element.url):
            targets.append(element.url)
            return list(element.content)
        return None

    for block in blocks:
        block.walk(action)
    return targets


# Parts


@dataclass
class Subquestion:
    label: str
    points: float
    blocks: list[pf.Block]
    """The label paragraph and everything up to the next label, options included."""


@dataclass
class Part:
    name: str
    """The file name without `.md`."""
    title: str
    context: list[pf.Block]
    """Without its local links, which `links` holds."""
    links: list[str]
    api: list[int]
    """The pandoc API version the part was read in."""
    subquestions: list[Subquestion] = field(default_factory=list)


def read_part(path: Path) -> Part:
    doc = read_doc(path)
    title = ""
    sections: dict[str, list[pf.Block]] = {}
    current: list[pf.Block] | None = None
    for block in doc.content:
        if isinstance(block, pf.Header) and block.level == 1 and not title:
            title = plain(block)
            continue
        if isinstance(block, pf.Header) and block.level == 2:
            current = sections.setdefault(plain(block).lower(), [])
            continue
        if current is not None:
            current.append(block)
    if not title:
        raise PaperError(f"{path}: no `# Part ...` heading")
    if "questions" not in sections:
        raise PaperError(f"{path}: no `## Questions` section")
    context = sections.get("context", [])
    part = Part(path.stem, title, context, unlink(context), list(doc.api_version))
    for block in sections["questions"]:
        found = label_of(block)
        if found:
            label, points = found
            part.subquestions.append(Subquestion(label.upper(), points, [block]))
        elif part.subquestions:
            part.subquestions[-1].blocks.append(block)
    if not part.subquestions:
        raise PaperError(f"{path}: no `**1A. [n marks]**` subquestions")
    return part


def label_of(block: pf.Block) -> tuple[str, float] | None:
    if not (isinstance(block, pf.Para) and block.content):
        return None
    first = block.content[0]
    match = LABEL.match(plain(first)) if isinstance(first, pf.Strong) else None
    return (match.group(1), float(match.group(2))) if match else None


def read_answers(path: Path) -> dict[str, str]:
    if not path.exists():
        raise PaperError(f"no solution file {path}")
    answers = {}
    for block in read_doc(path).content:
        match = ANSWER.match(bold_text(block) or "")
        if match:
            answers[match.group(1).upper()] = match.group(2).strip()
    return answers


# Questions


def is_option_list(block: pf.Block) -> bool:
    return isinstance(block, pf.OrderedList) and block.style == "LowerAlpha"


def option_blocks(item: pf.ListItem) -> list[pf.Block]:
    """An option's blocks without the hard line break that ends `a. Yes  `."""
    blocks = list(item.content)
    if blocks and isinstance(blocks[0], (pf.Plain, pf.Para)):
        inlines = blocks[0].content
        while inlines and isinstance(inlines[-1], (pf.LineBreak, pf.SoftBreak, pf.Space)):
            inlines.pop()
    return blocks


def correct_letters(answer: str, count: int, label: str) -> list[int]:
    letters = re.findall(r"\b([a-z])\b", answer.lower())
    indexes = sorted({ord(letter) - ord("a") for letter in letters})
    if not indexes or any(i >= count for i in indexes):
        raise PaperError(f"{label}: answer {answer!r} does not name options a..{chr(96 + count)}")
    return indexes


def variants(answer: str) -> list[str]:
    """An answer as written, with an ASCII minus and in other letter cases."""
    seen: dict[str, None] = {}
    for base in (answer, answer.replace(MINUS, "-")):
        for form in (base, base.lower(), base.capitalize(), base.upper()):
            seen.setdefault(form, None)
    return list(seen)


def numbered_answers(answer: str, count: int, label: str) -> list[str]:
    if count == 1 and not answer.startswith("("):
        return [answer]
    pairs = {int(n): text for n, text in NUMBERED_ANSWER.findall(answer)}
    if sorted(pairs) != list(range(1, count + 1)):
        raise PaperError(
            f"{label}: the stem has {count} blanks but the answer {answer!r}"
            f" numbers {sorted(pairs)}"
        )
    return [pairs[n] for n in range(1, count + 1)]


def mark_blanks(blocks: list[pf.Block]) -> int:
    """Replace each `______` in the blocks with a `{{n}}` blank marker; returns their count."""
    numbers = itertools.count(1)

    def action(element: pf.Element, _: pf.Doc) -> None:
        if isinstance(element, pf.Str):
            element.text = BLANK_RUN.sub(lambda _: f"{{{{{next(numbers)}}}}}", element.text)

    for block in blocks:
        block.walk(action)
    return next(numbers) - 1


def title_of(sub: Subquestion) -> str:
    title = plain(sub.blocks[0])
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT].rsplit(" ", 1)[0] + "..."
    return title


def split_options(sub: Subquestion) -> tuple[list[pf.Block], list[list[pf.Block]]]:
    """The stem's blocks and each option's blocks.

    Options start at the first `a.` list. An option whose text is set apart from its
    letter (`a.` then an unindented bullet list) reaches pandoc as an empty item and
    a separate block, and the next letter as a new list starting at `b`; so every
    block up to the next lettered list belongs to the option before it.
    """
    first = next((i for i, block in enumerate(sub.blocks) if is_option_list(block)), None)
    if first is None:
        return sub.blocks, []
    options: list[list[pf.Block]] = []
    for block in sub.blocks[first:]:
        if is_option_list(block) and block.start == len(options) + 1:
            options.extend(option_blocks(item) for item in block.content)
        elif is_option_list(block):
            raise PaperError(f"{sub.label}: option {chr(96 + block.start)}. is out of order")
        else:
            options[-1].append(block)
    return sub.blocks[:first], options


def generated_question(sub: Subquestion, answer: str | None, api: list[int]) -> dict[str, Any]:
    if answer is None:
        raise PaperError(f"{sub.label}: no `**{sub.label}. answer**` line in the solution")
    stem, options = split_options(sub)
    base: dict[str, Any] = {"id": sub.label, "title": title_of(sub), "points": sub.points}
    if options:
        correct = correct_letters(answer, len(options), sub.label)
        base |= {
            "type": "mc",
            "stem_html": to_html(stem, api),
            "choices": [
                {"html": to_html(blocks, api), "correct": i in correct}
                for i, blocks in enumerate(options)
            ],
        }
        stem_text = " ".join(plain(block) for block in stem)
        if len(correct) > 1 or SELECT_ALL.search(stem_text):
            base["scoring"] = "plus_minus"
        return base
    count = mark_blanks(stem)
    if not count:
        stem = [*stem, pf.Para(pf.Str("{{1}}"))]
        count = 1
    base |= {
        "type": "fitb",
        "stem_html": to_html(stem, api),
        "blanks": [
            {"answers": variants(text)} for text in numbered_answers(answer, count, sub.label)
        ],
    }
    if len(BLANK_MARKER.findall(base["stem_html"])) != count:
        raise PaperError(f"{sub.label}: blank markers were lost converting the stem")
    return base


def tab_title(title: str) -> str:
    title = PART_PREFIX.sub("", title).strip()
    return title if len(title) <= TAB_TITLE_MAX else title[: TAB_TITLE_MAX - 3] + "..."


def context_title(part: Part) -> str:
    """A context's own heading, a paragraph that is all bold (`**The Maze**`)."""
    for block in part.context:
        heading = bold_text(block)
        if heading is not None:
            return tab_title(heading)
        if not isinstance(block, (pf.Para, pf.Plain)) or plain(block):
            break
    return tab_title(part.title)


# Paper


def natural_key(path: Path) -> list[Any]:
    """Order file names with their numbers as numbers: q2 before q10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name)]


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PaperError(f"no config {path}; it needs at least `folder:`")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise PaperError(f"{path}: expected a mapping")
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise PaperError(f"{path}: unknown keys {sorted(unknown)}")
    if not config.get("folder"):
        raise PaperError(f"{path}: `folder` is required")
    for key in ("defaults", "parts", "questions"):
        if not isinstance(config.get(key) or {}, dict):
            raise PaperError(f"{path}: `{key}` must be a mapping")
    return config


def paper_spec(paper: Path, config_path: Path | None = None) -> dict[str, Any]:
    """The spec for a paper, ready to dump as YAML; case studies are shared objects."""
    config = load_config(config_path or paper / "examsoft.yaml")
    files = sorted((paper / "questions").glob("*.md"), key=natural_key)
    if not files:
        raise PaperError(f"no {paper / 'questions'}/*.md")
    parts = {f.name: read_part(f) for f in files}
    part_overrides: dict[str, Any] = config.get("parts") or {}
    question_overrides: dict[str, Any] = {
        str(k).upper(): v for k, v in (config.get("questions") or {}).items()
    }
    unknown_parts = set(part_overrides) - {part.name for part in parts.values()}
    labels = [sub.label for part in parts.values() for sub in part.subquestions]
    unknown_labels = set(question_overrides) - set(labels)
    if unknown_parts or unknown_labels:
        raise PaperError(
            f"config names parts {sorted(unknown_parts)} or questions {sorted(unknown_labels)}"
            " that the paper does not have"
        )
    duplicates = {label for label in labels if labels.count(label) > 1}
    if duplicates:
        raise PaperError(f"labels used more than once: {sorted(duplicates)}")

    questions: list[dict[str, Any]] = []
    for filename, part in parts.items():
        answers = read_answers(paper / "solutions" / filename)
        tabs = []
        for target in part.links:
            linked = parts.get(Path(target.split("#")[0]).name)
            if linked and linked is not part and linked.context:
                tabs.append(
                    {"title": context_title(linked), "html": to_html(linked.context, part.api)}
                )
        if part.context:
            tabs.append({"title": context_title(part), "html": to_html(part.context, part.api)})
        if len(tabs) > CASE_STUDY_TABS_MAX:
            raise PaperError(f"{filename}: more than {CASE_STUDY_TABS_MAX} case study tabs")
        case_study = tabs or None
        for sub in part.subquestions:
            generated = generated_question(sub, answers.get(sub.label), part.api)
            generated["group"] = part.title
            if case_study:
                generated["case_study"] = case_study
            override = question_overrides.get(sub.label) or {}
            questions.append({**(part_overrides.get(part.name) or {}), **generated, **override})
    defaults = {"folder": config["folder"], **(config.get("defaults") or {})}
    return {"defaults": defaults, "questions": questions}


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def dump_spec(spec: dict[str, Any]) -> str:
    """YAML with long HTML as literal blocks; a shared case study is written once (&anchor)."""
    return yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
