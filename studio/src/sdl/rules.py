"""Rulebook: limits measured from the reference library (docs/references.md), not guessed.

Source of numbers:
- word counts: slide-reference-library/inspection/*-text.json, research-talk decks
  (sortformer, physics, induction, brain-tuning, layer-by-layer): median 35–70 words/slide,
  'good'-labelled slides 6–71 words except data-heavy results (164 incl. numbers).
- Morph timing: SlidesCarnival 'Bold Text and Color Morph' (17/17 slides: byObject, 2000 ms),
  PELDA.pptx (byObject, 2000 ms). We use 1200 ms for talks (20 min, ~14 slides), 1600 ms for world-Morph.
- object counts per slide in the Morph template: median 11, max 76 (decorative heavy) ->
  decoration budget below keeps us well under the median.
"""

WORD_LIMITS = {          # words of visible text (labels on charts/diagrams excluded)
    "title": 22, "section": 8, "statement": 20, "closing": 30, "content": 45, "figure": 25,
    "figure_text": 40, "comparison": 45, "formula": 30, "evidence": 80, "credits": 140,
}
MIN_FONT_PT = {"content": 14, "chrome": 10, "decoration": 8, "motif": 8, "world": 8}
MIN_CONTRAST = 4.5          # WCAG AA normal text
MIN_CONTRAST_LARGE = 3.0    # >= 24 pt
MAX_BUILDS = 3
MAX_ZOOM_PER_TRANSITION = 3.0
MOTIF_BUDGET = {"max_slides_ratio": 0.4, "max_per_slide": 1, "quiet_types": {"evidence", "formula", "credits"}}
DECORATION_PER_SLIDE = 3    # non-content, non-chrome objects
ACCENTS_PER_SLIDE = 1       # one accent hue besides 'ours'
CHART_RULES = ["direct labels, no legend", "no gridlines/frame", "ours = accent, rest = baseline grey",
               "one chart = one claim (title states the claim)"]
