"""Bridge to the design side (runs in-process on the PowerPoint laptop).

Design plugs in by setting  P2S_DESIGN_BACKEND="their_package.module:render"
with the signature:

    def render(deck: dict, run_dir: str) -> dict:
        # deck: validated Deck dict (see p2s/contracts.py / slide_spec.schema.json)
        return {"pptx_path": "...", "issues": [Feedback-like dicts]}

Issues whose slide_id matches a slide are routed back to the content agent
(e.g. {"slide_id": "s04", "issue": "overflow", "field": "bullets[1]", "limit": 18,
"actual": 25, "message": "text box overflows", "source": "judge"}).

Without a backend we write a plain python-pptx draft so the pipeline is testable.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path


def _load_backend():
    spec = os.environ.get("P2S_DESIGN_BACKEND")
    if not spec:
        return None
    mod, fn = spec.split(":")
    return getattr(importlib.import_module(mod), fn)


def render_deck(deck: dict, run_dir: str) -> dict:
    backend = _load_backend()
    if backend:
        return backend(deck, run_dir)
    return _draft_pptx(deck, run_dir)


def design_hook(name: str):
    """Optional extra functions next to the backend's render (same module), or None:

    guide(state) -> {"types": str, "limits": {type: max_chars}, "body_types": [...], "fixed": [...]}
        the design side's slide types and their content slots, written into the agents' prompts
    check(deck, run_dir) -> [Feedback dicts]
        design-side checks before the human review (limits, slots, measured fit with the chosen fonts)
    """
    spec = os.environ.get("P2S_DESIGN_BACKEND")
    if not spec:
        return None
    return getattr(importlib.import_module(spec.split(":")[0]), name, None)


def _draft_pptx(deck: dict, run_dir: str) -> dict:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for s in deck["slides"]:
        layout = prs.slide_layouts[0 if s["type"] in ("title", "section_divider", "thank_you") else 1]
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = s["title"]
        body = slide.placeholders[1] if len(slide.placeholders) > 1 else None
        if body is not None:
            if s["type"] == "title":
                p = deck["paper"]
                body.text = f"{', '.join(p['authors'])}\n{p['venue']}\n{' / '.join(deck.get('team', []))}"
            else:
                body.text = "\n".join(s.get("bullets", []))
                if s.get("figures"):
                    body.width = Inches(6)
        for f in s.get("figures", [])[:1]:
            fp = Path(f["path"]) if Path(f["path"]).is_absolute() else Path(run_dir) / f["path"]
            if fp.exists():
                slide.shapes.add_picture(str(fp), Inches(7), Inches(1.8), width=Inches(5.8))
        if s.get("notes"):
            slide.notes_slide.notes_text_frame.text = s["notes"]
    out = Path(run_dir) / "draft.pptx"
    prs.save(out)
    return {"pptx_path": str(out), "issues": []}
