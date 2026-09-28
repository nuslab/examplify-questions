# examsoft-questions

Creates ExamSoft (Examplify) questions from a YAML spec by driving an
ExamSoft portal with Playwright:

- `validate` checks a spec offline.
- `folders` lists the question folders your account can add to.
- `create` fills each question's editor in the portal, saves it as a draft
  (or approves it with `--approve`), reads it back from its edit page and
  reports anything that differs from the spec. `--dry-run` fills and checks
  the editors without saving.
- `verify` finds the spec's questions in ExamSoft and compares them with the
  spec again.

Every title ends with a tag such as `[2btdfu]`, a short hash of the question's
folder path and its spec `id`. `create` searches ExamSoft for each tag first
and skips questions that already exist, so a run that stopped part-way can
simply be repeated, from any machine. The tag does not depend on the question's
content: editing a question in the spec keeps its tag, and does not update the
portal copy (`verify` shows the differences). Moving a question to another
folder, in the spec or in the portal, or removing the tag from its title, makes
the next `create` add it again.

Multiple choice (one or several correct answers), fill in the blank (text and
numeric-range blanks) and essay questions are supported, each optionally with a
case study artefact.

## Install

```
pip install -e '.[dev]'
python -m playwright install chromium
```

## Use

```
examsoft-questions validate exam.yaml
examsoft-questions folders --match CS101
examsoft-questions create exam.yaml --dry-run
examsoft-questions create exam.yaml
examsoft-questions verify exam.yaml
```

The browser opens with a persistent profile (`--profile`, by default
`../data/examsoft-profile`) and signs in through SSO when the session has
lapsed; complete any SSO prompts in the browser. ExamSoft's session cookies are
kept in the profile's `examsoft-session.json`, since Chromium drops session
cookies at start and ExamSoft asks SSO for a password sign-in every time. `--pause` waits for Enter
before each save so you can look at the filled editor. `--only ID ...` limits a
run to some questions.

## Spec

```yaml
defaults:                      # applied to every question that has the field
  folder: 2026 Fall/CS101/Final
  points: 1

questions:
  - id: q1a                    # the spec's own key; with the folder, it makes the title tag
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

## How it works

The portal's editors are driven through the page's own controls and scripts
(jQuery, CKEditor 3, fancytree), so ExamSoft's own validation runs on every
save and its messages are reported as errors. `page.js` holds the scripts run
in the editor. The folder tree comes from the endpoint the folder picker uses,
and the picker is then set to the resolved folder. Tags are looked up with the
portal's keyword search, keeping only results whose title holds the exact
`[tag]`.

Opening an editor locks the question to the session, and ExamSoft releases
locks only at logout, so every run releases its locks when it finishes.
`verify` takes the lock the way the portal does before opening an editor, and
skips a question that is open in another session instead of taking it over.

## Development

```
ruff format --check . && ruff check . && mypy && pytest
```

Tests cover the spec, folder resolution, tags and the comparison of a
read-back question with its spec; they need no ExamSoft account.
