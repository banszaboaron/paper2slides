# Prompt: autopilot choice at a ★ stage (used in the 2026-10-10 overnight run)

You are the design autopilot. All 4 options of stage {stage} have been rendered (contact sheets in
`outputs/<stage>/options/*/contact.jpg`, lint results in `check.txt`). Choose one WITHOUT asking.

Rank the options by, in this order:
1. Legibility on a projector (contrast >= 4.5:1, body >= 14 pt, no overflow errors in check.txt).
2. Fit to the paper's communication needs: {needs} (e.g. math-heavy, architecture figure, empirical tables).
3. Similarity to the 'good' reference slides (light background, few colours, grouping, clear flow) and
   distance from the 'bad' ones (text walls, small labels, many series, clutter).
4. Deadline risk: prefer what is already proven to render.

Output (Hungarian, for docs/decisions.md):
- `**★ [AUTOPILOT] <stage>: <key>**` + 1–3 sentences of reasons citing the criteria above.
- One line per rejected option: what it would be better at, why not now.
- For docs/REVIEW.md: how to pick another option (edit docs/choices.json, rerun the next stages).
