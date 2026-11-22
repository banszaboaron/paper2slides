# Portfolio status: paper2slides · Slide Studio

A fresh repository (one initial commit) made from the finished portfolio version of the course project, so
that no earlier, non-redistributable demo material is in its history. Published at
https://github.com/banszaboaron/paper2slides.

## What the portfolio work added (on top of the course version)

- Late fixes that never reached the course version (CLI schema fix, credits regex, design edits).
- `studio/tools/headless_run.py`: the whole Studio flow over the same MCP servers as the UI;
  `record_replay.py` for offline replays; `eval/evaluate.py`; font licences.
- The visual judge's layout fixes are rejected if they would drop any visible word.
- Zoom crops snap to blank rows/columns (`snap_region`); figure crops keep panel titles and y-axis labels;
  the credits name the model actually used.
- Judge fixes run in a child process (a Windows hang); `--finish`; `--reuse-design` keeps the presenters.
- Offline tests for the fixes; `scripts/demo.cmd`.
- Provenance: each design choice records who made it; scripted runs say "auto-approved" / "auto-picked"
  on the slide itself.
- The Minerva example deck, its replay data, the README and this file.

## Verification report (what was actually run)

| Check | Result |
|---|---|
| `pytest -q` (fake provider, no network) | 8 passed |
| Live run A, single pass (Claude Code CLI, Sonnet) | finished; deck exported by PowerPoint |
| Live run B, checked pipeline (same design choices) | finished; deck exported by PowerPoint |
| Evaluation of A and B (`eval/evaluate.py`) | see README, *Evaluation*; saved in `eval/results/` |
| Number context review | done by an LLM (Claude Opus), **not** by a person |
| Rendered slides looked at | all slides of B (PowerPoint PNGs); the workflow slide fixed and re-checked |
| Offline demo `scripts\demo.cmd` from a clean session | passed: 145 s, the judge applied 10 fixes (0 rejected), PowerPoint export of 16 slides; slide text identical to `examples/minerva/minerva.pptx` |
| `.pptx` editability | 99.5 % of spec words are native text (python-pptx); charts and tables native; display formulas are pictures |
| Morph | PowerPoint reports the Morph entry effect on slides 2–16 at export; not watched in a slide show |
| LibreOffice fallback | not tested |
| Example files free of local paths | `deck.json`, `script.md`, `.pptx` XML checked; `.pptx` metadata now neutral ("paper2slides") |

Cost of this portfolio work in model calls: two live Claude Code CLI runs (subscription; about $1.01 + $1.42
API-equivalent as reported by the CLI) and one LLM context review of the 64 numbers. No paid API key was used.

## Open items

1. **Human review**: `eval/results/*_numbers.csv` and `*_review.csv` have empty `verdict / reviewer`
   columns for a person. Until filled, the README says "LLM review, not human".

The GeoRA figures in `studio/fixture/geora/` (test fixture) were checked by the owner: the paper's licence
allows redistribution.

## CV bullets (each one points to something checkable in the repository)

- Built **paper2slides / Slide Studio**, an LLM-agent system that turns a research paper into an editable,
  source-checked PowerPoint talk: six MCP tool servers, a deterministic layout engine with measured text
  fit and PowerPoint rendering, integrated with a LangGraph content pipeline; on the evaluated paper the checked
  pipeline produced 0 numbers absent from the source and 0 layout overflows.
- Designed a small reproducible evaluation (number grounding with source passages, layout, editability,
  runtime and cost; single-pass vs checked generation) and used it to find that an LLM visual reviewer's
  layout fixes silently deleted slide content; fixed it with a deterministic word-preservation guard.

## Possible next steps (not started)

- A second paper (e.g. the GeoRA paper, CC BY) to make the comparison n = 2.
- Native OMML equations for display formulas.
- Test the LibreOffice fallback on a machine without PowerPoint.
