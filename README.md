# examsoft-questions

Creates ExamSoft (Examplify) questions from a YAML spec by driving an
ExamSoft portal with Playwright:

- `import-md` converts a paper written in pandoc Markdown into a spec.
- `validate` checks a spec offline.
- `folders` lists the question folders your account can add to.
- `create` fills each question's editor, saves it as a draft (or approves it
  with `--approve`), and reports anything on its edit page that differs from the
  spec. `--dry-run` fills and checks the editors without saving.
- `update` refills and saves the questions that differ from the spec. A draft is
  saved in place; an approved question gets a new revision and is approved
  again, so assessments can use it. It cannot change the number or kinds of a
  fill-in-the-blank question's blanks.
- `verify` compares the questions in ExamSoft with the spec.

`update` and `verify` skip a question that is open in another session.

Every title ends with a tag such as `[2btdfu]`, a short hash of the question's
folder path and spec `id`. `create` skips questions whose tag ExamSoft already
has, so a run that stopped part-way can be repeated from any machine. Editing a
question in the spec keeps its tag; moving it to another folder, or removing the
tag from its title in the portal, makes the next `create` add it again.

Multiple choice (one or several correct answers), fill in the blank (text and
numeric-range blanks) and essay questions are supported, each optionally with a
case study artefact. Formulas are written as MathML, since the portal has no
LaTeX conversion, and become formula images.

## Install

```
pip install -e '.[dev]'
python -m playwright install chromium
```

`import-md` needs pandoc on `PATH`, or `pip install -e '.[markdown]'` for a
bundled copy.

## Use

```
examsoft-questions import-md paper/ -o exam.yaml
examsoft-questions validate exam.yaml
examsoft-questions folders --match CS101
examsoft-questions create exam.yaml --dry-run
examsoft-questions create exam.yaml
examsoft-questions update exam.yaml
examsoft-questions verify exam.yaml
```

The browser opens with a persistent profile (`--profile`, by default
`../data/examsoft-profile`) and signs in through SSO when the session has
lapsed; complete any SSO prompts in the browser. The session is kept in the
profile's `examsoft-session.json` between runs.

`--only ID ...` limits a run to some questions. `--pause` waits for Enter before
each save, to look at the filled editor.

## Spec

```yaml
defaults:                      # applied to every question that has the field
  folder: 2026 Fall/CS101/Final
  points: 1

questions:
  - id: q1a
    type: mc
    title: "1A. [2 marks] Primes"
    stem: |
      Which of these numbers are prime?

      Select all that apply.
    choices:
      - {text: "2", correct: true}
      - {text: "4"}
      - {text: "7", correct: true}
      - {text: None of the above, locked: true}
    scoring: partial
    randomize_choices: true
    points: 2
    calculator: scientific
    rationale: 2 and 7 have no divisors other than 1 and themselves.

  - id: q2
    type: fitb
    stem: The capital of France is {{1}}, and pi to within 0.01 is {{2}}.
    blanks:
      - answers: [Paris, paris]
      - range: [3.13, 3.15]
    partial_credit: true

  - id: q3
    type: essay
    stem_html: <p>Explain why <strong>A*</strong> is optimal.</p>
    char_limit: 1500
    calculator: both
    case_study:
      - title: Scenario
        text: A robot plans a route on a grid. Each move costs 1.
      - title: Heuristic
        html: <p>h(n) is the <em>Manhattan distance</em> to the goal.</p>
```

Fields for every type:

| Field | Meaning |
| --- | --- |
| `id` | Required, unique in the spec; part of the title tag, so keep it once the question is created. |
| `type` | `mc`, `fitb` or `essay`. |
| `folder` | Required. `/`-separated path; any unique tail of the full path is enough. With `create --create-folders`, missing folders at the end of the path are created. |
| `stem` / `stem_html` | Exactly one. `stem` is plain text: blank lines separate paragraphs. `stem_html` goes into the editor as is. |
| `title` | ExamSoft's title, before the tag; the stem's first 20 characters when left out. |
| `points` | ExamSoft's weight, 1 by default. |
| `calculator` | `none`, `scientific`, `graphing` or `both`. |
| `spreadsheet` | Enable Examplify's spreadsheet tool. |
| `group` | Keeps questions with the same group together on randomized assessments; set it in `defaults` to group a whole spec. |
| `cut_score` | Between 0 and 1. |
| `rationale` | ExamSoft's Rationale field. |
| `case_study` | ExamSoft's case study artefact: 1 to 5 tabs shown beside the question, each with a `title` (at most 30 characters) and `text` or `html`. |

Multiple choice (`mc`):

| Field | Meaning |
| --- | --- |
| `choices` | At least two, each with `text` or `html`, and optional `correct` and `locked` (kept in place when choices are randomized). At least one must be correct. |
| `scoring` | For several correct choices: `partial` (Partial Credit; the default), `all_or_nothing` (Select All That Apply) or `plus_minus` (+/- Partial Credit). Leave it out for a single-answer question. |
| `randomize_choices` | Shuffle the choices for each exam taker. |

Fill in the blank (`fitb`): the stem marks blank *n* of `blanks` as `{{n}}`,
each exactly once. A blank has either `answers` (accepted texts) or `range`
(lower and upper limit of an accepted number; the lower must be below the
upper). `partial_credit` gives credit per correct blank.

Essay (`essay`): `char_limit` caps the answer's length in Examplify.

## Papers in Markdown

`import-md PAPER` reads a paper laid out as `PAPER/questions/*.md`, one file per
part, with the answers in `PAPER/solutions/` under the same file names:

```markdown
# Part 2: Graphs

## Context

**The Maze**

A maze has \(n \geq 2\) cells...

## Questions

**2A. [1 mark]** Is the maze connected?

a. Yes  
b. No

**2B. [2 marks]** Which cells are dead ends? Select all that apply.

a. Cell 1  
...

**2C. [2 marks]** The shortest path visits (1) ______ cells and costs (2) ______.
```

```markdown
**2A. a**

**2B. a, c**

**2C. (1) 4, (2) −3**
```

- Each part is a question group, and its Context is a case study tab on every
  subquestion, titled by the Context's bold first line. A link in the Context
  to another part's file adds that part's Context as an earlier tab.
- A subquestion with `a.`, `b.`, ... options is multiple choice. It is scored
  with +/- Partial Credit when it says "select all that apply" or has several
  correct options; "None of the above" is an ordinary option.
- A subquestion without options is fill in the blank: each `______` in the stem
  is a blank, answered in order by `(1) ..., (2) ...`, or a single blank is
  added after the stem. Answers are accepted as written, with an ASCII minus,
  and in lower, capitalised and upper case.
- Points come from `[n marks]`, and the title is the label and the start of the
  stem. TeX formulas become MathML, and local links keep only their text.

`PAPER/examsoft.yaml` (or `--config`) adds what the Markdown does not say:

```yaml
folder: 2026 Fall/CS101/Quiz       # required
defaults: {calculator: none}                # any question field, for every question
parts:                                      # per part file, without .md
  q2-graphs: {calculator: scientific}
questions:                                  # per label; replaces generated fields
  2C: {blanks: [{answers: ["4", four]}, {range: [-3.01, -2.99]}]}
```

An entry for a part or label the paper does not have is an error.

## Development

```
ruff format --check . && ruff check . && mypy && pytest
```

The portal is driven through its own page scripts (jQuery, CKEditor 3,
fancytree), so ExamSoft's validation runs on every save; `page.js` holds the
scripts run in the editor. Tests need pandoc but no ExamSoft account.
