---
name: paper2slides
description: Turn a research paper PDF into a conference slide deck using the team's LangGraph pipeline (outline -> content -> validation -> design). Use when the user wants slides/a presentation from a paper, asks about the status of a slide run, or answers a pending choice for a run.
---

# paper2slides

You drive a local LangGraph pipeline through its CLI. The pipeline lives in
`{{P2S_ROOT}}` and every command prints JSON.

Run commands with the exec tool exactly like this (Windows, PowerShell or cmd):

```
"{{P2S_ROOT}}\scripts\p2s.cmd" <command> [args]
```

## Commands

| Goal | Command |
|---|---|
| Start a run | `run --pdf "<abs path to pdf>" --team "Name A,Name B" --venue "ICLR 2026 (Oral)" [--notes "style notes"]` |
| Check a run | `status <run_id>` |
| Answer a pending choice | `resume <run_id> "<answer>"` |
| List runs | `list` |

`run` and `resume` block until the pipeline either finishes or needs a human decision.
They can take several minutes with real models.

## Human decisions (important)

When the JSON has `"state": "waiting_for_human"`, the pipeline is paused on a LangGraph
`interrupt()`. Do NOT answer it yourself. Show the user:

1. `pending[0].question`
2. the options (`pending[0].options[*].label`)
3. a compact view of `pending[0].context`: for `outline_review` the slide list
   (id, type, goal, figure_ids); for `deck_review` slide titles and any
   `unresolved_feedback`.

Then pass the user's reply back verbatim with one of these answer formats:

- `approve` or `approve: <extra style notes>`
- `revise: <what to change, mention slide ids like s04>`
- `abort` (only on `deck_review`)

The user can also answer in the browser at http://127.0.0.1:8766 (started with
`scripts\p2s.cmd ui`). If `status` shows the run already moved on, it was answered there.

## Reporting

When `"state": "done"`, give the user `pptx_path`, `deck_json`, and the `trace`
file path (JSONL step log used for the workflow slide). Report errors as-is; do not
retry a failing run more than once without asking.
