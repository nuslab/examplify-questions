"""Convert a paper written in pandoc Markdown into a question spec.

A paper directory holds `questions/*.md`, one file per part, the answers in
`solutions/*.md` under the same names, and `examsoft.yaml` for what the Markdown
does not say (see the README).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import BLANK_MARKER

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
TAB_TITLE_LIMIT = 30
TAB_LIMIT = 5
TITLE_LIMIT = 60
CONFIG_KEYS = {"folder", "defaults", "parts", "questions"}
GENERATED = {"id", "type", "title", "stem_html", "choices", "blanks", "points", "scoring", "group"}

Block = dict[str, Any]
Inline = dict[str, Any]


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


def read_ast(path: Path) -> dict[str, Any]:
    ast: dict[str, Any] = json.loads(
        pandoc(["-f", READER, "-t", "json"], path.read_text(encoding="utf-8"))
    )
    return ast


class Html:
    """Blocks to HTML with MathML formulas, in one pandoc run per document."""

    def __init__(self, api: list[int]) -> None:
        self.api = api

    def __call__(self, blocks: list[Block]) -> str:
        document = {"pandoc-api-version": self.api, "meta": {}, "blocks": blocks}
        args = ["-f", "json", "-t", "html", "--mathml", "--wrap=none"]
        return pandoc(args, json.dumps(document)).strip()


# Inline text


def plain(inlines: list[Inline]) -> str:
    """Inline text as a reader sees it, with formulas as their TeX source."""
    out: list[str] = []
    for inline in inlines:
        kind: str = inline["t"]
        content: Any = inline.get("c")
        if kind == "Str":
            out.append(content)
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif kind in ("Math", "Code"):
            out.append(content[1])
        elif kind in (
            "Emph",
            "Strong",
            "Underline",
            "Strikeout",
            "SmallCaps",
            "Superscript",
            "Subscript",
        ):
            out.append(plain(content))
        elif kind in ("Link", "Span"):
            out.append(plain(content[1]))
        elif kind == "Quoted":
            out.append(f'"{plain(content[1])}"')
    return " ".join("".join(out).split())


def block_text(block: Block) -> str:
    if block["t"] in ("Para", "Plain"):
        return plain(block["c"])
    if block["t"] == "Header":
        return plain(block["c"][2])
    return ""


def local_links(blocks: list[Block]) -> Iterator[str]:
    """The target of every link in the blocks that is not a URL or an anchor."""
    stack: list[Any] = list(blocks)
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if node.get("t") == "Link":
                target = node["c"][2][0]
                if not EXTERNAL.match(target):
                    yield target
            stack.extend(node.values())


def unlink(node: Any) -> Any:  # noqa: ANN401 - pandoc's JSON AST
    """Local links cannot resolve in ExamSoft: keep only their text."""
    if isinstance(node, list):
        out: list[Any] = []
        for item in node:
            if (
                isinstance(item, dict)
                and item.get("t") == "Link"
                and not EXTERNAL.match(item["c"][2][0])
            ):
                out.extend(unlink(item["c"][1]))
                continue
            out.append(unlink(item))
        return out
    if isinstance(node, dict):
        return {key: unlink(value) for key, value in node.items()}
    return node


# Parts


@dataclass
class Subquestion:
    label: str
    points: float
    blocks: list[Block]
    """The label paragraph and everything up to the next label, options included."""


@dataclass
class Part:
    name: str
    """The file name without `.md`."""
    title: str
    context: list[Block]
    subquestions: list[Subquestion] = field(default_factory=list)


def read_part(path: Path) -> tuple[Part, list[int]]:
    ast = read_ast(path)
    title = ""
    sections: dict[str, list[Block]] = {}
    current: list[Block] | None = None
    for block in ast["blocks"]:
        if block["t"] == "Header" and block["c"][0] == 1 and not title:
            title = block_text(block)
            continue
        if block["t"] == "Header" and block["c"][0] == 2:
            current = sections.setdefault(block_text(block).lower(), [])
            continue
        if current is not None:
            current.append(block)
    if not title:
        raise PaperError(f"{path}: no `# Part ...` heading")
    if "questions" not in sections:
        raise PaperError(f"{path}: no `## Questions` section")
    part = Part(path.stem, title, sections.get("context", []))
    for block in sections["questions"]:
        found = label_of(block)
        if found:
            label, points = found
            part.subquestions.append(Subquestion(label.upper(), points, [block]))
        elif part.subquestions:
            part.subquestions[-1].blocks.append(block)
    if not part.subquestions:
        raise PaperError(f"{path}: no `**1A. [n marks]**` subquestions")
    return part, ast["pandoc-api-version"]


def label_of(block: Block) -> tuple[str, float] | None:
    if block["t"] != "Para" or not block["c"] or block["c"][0]["t"] != "Strong":
        return None
    match = LABEL.match(plain(block["c"][0]["c"]))
    return (match.group(1), float(match.group(2))) if match else None


def read_answers(path: Path) -> dict[str, str]:
    if not path.exists():
        raise PaperError(f"no solution file {path}")
    answers = {}
    for block in read_ast(path)["blocks"]:
        if block["t"] == "Para" and len(block["c"]) == 1 and block["c"][0]["t"] == "Strong":
            match = ANSWER.match(plain(block["c"][0]["c"]))
            if match:
                answers[match.group(1).upper()] = match.group(2).strip()
    return answers


# Questions


def is_option_list(block: Block) -> bool:
    return bool(block["t"] == "OrderedList" and block["c"][0][1]["t"] == "LowerAlpha")


def option_blocks(item: list[Block]) -> list[Block]:
    """An option's blocks without the hard line break that ends `a. Yes  `."""
    blocks = [dict(block) for block in item]
    if blocks and blocks[0]["t"] in ("Plain", "Para"):
        inlines = list(blocks[0]["c"])
        while inlines and inlines[-1]["t"] in ("LineBreak", "SoftBreak", "Space"):
            inlines.pop()
        blocks[0] = {"t": blocks[0]["t"], "c": inlines}
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


def mark_blanks(blocks: list[Block]) -> tuple[list[Block], int]:
    """Replace each `______` in the blocks with a `{{n}}` blank marker."""
    count = 0

    def walk(node: Any) -> Any:  # noqa: ANN401 - pandoc's JSON AST
        nonlocal count
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            if node.get("t") == "Str" and BLANK_RUN.search(node["c"]):

                def number(_: re.Match[str]) -> str:
                    nonlocal count
                    count += 1
                    return "{{" + str(count) + "}}"

                return {"t": "Str", "c": BLANK_RUN.sub(number, node["c"])}
            return {key: walk(value) for key, value in node.items()}
        return node

    return walk(blocks), count


def title_of(sub: Subquestion) -> str:
    title = plain(sub.blocks[0]["c"])
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT].rsplit(" ", 1)[0] + "..."
    return title


def split_options(sub: Subquestion) -> tuple[list[Block], list[list[Block]]]:
    """The stem's blocks and each option's blocks.

    Options start at the first `a.` list. An option whose text is set apart from its
    letter (`a.` then an unindented bullet list) reaches pandoc as an empty item and
    a separate block, and the next letter as a new list starting at `b`; so every
    block up to the next lettered list belongs to the option before it.
    """
    first = next((i for i, block in enumerate(sub.blocks) if is_option_list(block)), None)
    if first is None:
        return sub.blocks, []
    options: list[list[Block]] = []
    for block in sub.blocks[first:]:
        if is_option_list(block) and block["c"][0][0] == len(options) + 1:
            options.extend(option_blocks(item) for item in block["c"][1])
        elif is_option_list(block):
            letter = chr(96 + block["c"][0][0])
            raise PaperError(f"{sub.label}: option {letter}. is out of order")
        else:
            options[-1].append(block)
    return sub.blocks[:first], options


def question(sub: Subquestion, answer: str | None, html: Html) -> dict[str, Any]:
    if answer is None:
        raise PaperError(f"{sub.label}: no `**{sub.label}. answer**` line in the solution")
    stem, options = split_options(sub)
    base: dict[str, Any] = {"id": sub.label, "title": title_of(sub), "points": sub.points}
    if options:
        correct = correct_letters(answer, len(options), sub.label)
        base |= {
            "type": "mc",
            "stem_html": html(stem),
            "choices": [
                {"html": html(blocks), "correct": i in correct} for i, blocks in enumerate(options)
            ],
        }
        stem_text = " ".join(block_text(block) for block in stem)
        if len(correct) > 1 or SELECT_ALL.search(stem_text):
            base["scoring"] = "plus_minus"
        return base
    marked, count = mark_blanks(stem)
    if not count:
        marked = [*marked, {"t": "Para", "c": [{"t": "Str", "c": "{{1}}"}]}]
        count = 1
    base |= {
        "type": "fitb",
        "stem_html": html(marked),
        "blanks": [
            {"answers": variants(text)} for text in numbered_answers(answer, count, sub.label)
        ],
    }
    if len(BLANK_MARKER.findall(base["stem_html"])) != count:
        raise PaperError(f"{sub.label}: blank markers were lost converting the stem")
    return base


def tab_title(title: str) -> str:
    title = PART_PREFIX.sub("", title).strip()
    return title if len(title) <= TAB_TITLE_LIMIT else title[: TAB_TITLE_LIMIT - 3] + "..."


def context_title(part: Part) -> str:
    """A context's own heading, a paragraph that is all bold (`**The Maze**`)."""
    for block in part.context:
        if block["t"] == "Para" and len(block["c"]) == 1 and block["c"][0]["t"] == "Strong":
            return tab_title(plain(block["c"][0]["c"]))
        if block["t"] not in ("Para", "Plain") or block_text(block):
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


def convert(paper: Path, config_path: Path | None = None) -> dict[str, Any]:
    """The spec for a paper, ready to dump as YAML; case studies are shared objects."""
    config = load_config(config_path or paper / "examsoft.yaml")
    files = sorted((paper / "questions").glob("*.md"), key=natural_key)
    if not files:
        raise PaperError(f"no {paper / 'questions'}/*.md")
    parts: dict[str, tuple[Part, list[int]]] = {f.name: read_part(f) for f in files}
    part_overrides: dict[str, Any] = config.get("parts") or {}
    question_overrides: dict[str, Any] = {
        str(k).upper(): v for k, v in (config.get("questions") or {}).items()
    }
    unknown_parts = set(part_overrides) - {part.name for part, _ in parts.values()}
    labels = [sub.label for part, _ in parts.values() for sub in part.subquestions]
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
    for filename, (part, api) in parts.items():
        html = Html(api)
        answers = read_answers(paper / "solutions" / filename)
        tabs = []
        for target in local_links(part.context):
            linked = parts.get(Path(target.split("#")[0]).name)
            if linked and linked[0] is not part and linked[0].context:
                tabs.append(
                    {"title": context_title(linked[0]), "html": html(unlink(linked[0].context))}
                )
        if part.context:
            tabs.append({"title": context_title(part), "html": html(unlink(part.context))})
        if len(tabs) > TAB_LIMIT:
            raise PaperError(f"{filename}: more than {TAB_LIMIT} case study tabs")
        case_study = tabs or None
        for sub in part.subquestions:
            generated = question(sub, answers.get(sub.label), html)
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


def dump(spec: dict[str, Any]) -> str:
    """YAML with long HTML as literal blocks; a shared case study is written once (&anchor)."""
    return yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
