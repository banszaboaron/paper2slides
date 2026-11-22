"""Content side of the workflow: per-type character limits (kick-off) and the slide spec.

The content team writes the logical slide spec *before* the design stages, so every ★ preview
shows the real deck. Limits are font-independent character counts derived from the measured
word limits in rules.py; they are deliberately conservative so that no ★ font choice breaks them
(the actual fit is still measured per option at render time).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import rules, templates

DENSITY = {"tight": 0.75, "medium": 1.0, "dense": 1.3}
CHARS_PER_WORD = 6.5          # average technical-English word incl. the following space
TEXT_SKIP = {"image", "image_name", "meaning", "id", "variant", "role", "name", "latex", "chart", "build",
             "builds", "region", "ours_rows", "lines",
             "source", "authors", "reference"}   # small-print attribution lines are not counted
MARKUP = re.compile(r"==|\*\*|\$")


def char_limits(density: str = "medium", max_chars: int | None = None) -> dict[str, int]:
    """Per slide type max visible characters. max_chars sets the 'content' slide; others scale with it."""
    base_words = rules.WORD_LIMITS["content"]
    content_chars = max_chars or round(base_words * CHARS_PER_WORD * DENSITY[density])
    scale = content_chars / base_words
    out = {t: int(round(w * scale, -1)) for t, w in rules.WORD_LIMITS.items()}
    out["diagram"] = out["figure_text"]
    out["credits"] = max(out["credits"], 600)          # credits are reference lists, not talk text
    return out


def visible_text(c, key: str = "") -> list[str]:
    """Strings that end up on the slide (chart labels, latex, paths and metadata excluded)."""
    if key in TEXT_SKIP:
        return []
    if isinstance(c, str):
        return [c]
    if isinstance(c, dict):
        out = []
        for k, v in c.items():
            if k == "text" and isinstance(v, str):
                out.append(v)
            else:
                out += visible_text(v, k)
        return out
    if isinstance(c, (list, tuple)):
        if key == "terms":                               # formula terms: (latex symbol, explanation)
            return [t[1] for t in c if isinstance(t, (list, tuple)) and len(t) > 1]
        return [s for x in c for s in visible_text(x, key)]
    return []


def chars(ls: dict) -> int:
    return sum(len(MARKUP.sub("", s).strip()) for s in visible_text(ls.get("content", {})))


def _strings(x, skip=()) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [t for k, v in x.items() if k not in skip for t in _strings(v, skip)]
    if isinstance(x, list):
        return [t for v in x for t in _strings(v, skip)]
    return []


def validate_spec(deck: dict, limits: dict[str, int], base: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    slides = deck.get("slides") or []
    if not slides:
        return ["spec has no slides"], []
    ids = [s.get("id") for s in slides]
    for d in {i for i in ids if ids.count(i) > 1}:
        errs.append(f"duplicate slide id '{d}'")
    for k, s in enumerate(slides, 1):
        sid = s.get("id", f"#{k}")
        t = s.get("type")
        if t not in templates.LIBRARY:
            errs.append(f"{sid}: unknown type '{t}' (use {', '.join(templates.LIBRARY)})")
            continue
        v = s.get("variant")
        if v and v not in templates.LIBRARY[t]:
            errs.append(f"{sid}: variant '{v}' does not exist for {t} ({', '.join(templates.LIBRARY[t])})")
        c = s.get("content") or {}
        n, lim = chars(s), limits.get(t)
        if lim and n > lim:
            errs.append(f"{sid} ({t}): {n} characters > limit {lim} – shorten by {n - lim}")
        elif lim and n > 0.9 * lim:
            warns.append(f"{sid} ({t}): {n}/{lim} characters – close to the limit")
        if "image" in c:
            p = Path(c["image"])
            p = p if p.is_absolute() else base / p
            if not p.exists():
                errs.append(f"{sid}: image not found: {c['image']}")
            if not c.get("meaning"):
                errs.append(f"{sid}: image needs a 'meaning' (what it explains)")
        builds = {x.get("build") for x in (c.get("columns") or []) if isinstance(x, dict) and x.get("build")}
        builds |= set((c.get("builds") or {}).values()) if isinstance(c.get("builds"), dict) else set()
        if len(builds) > rules.MAX_BUILDS:
            errs.append(f"{sid}: {len(builds)} builds > {rules.MAX_BUILDS}")
        if t not in ("title", "section", "closing", "credits", "statement") and not c.get("title"):
            warns.append(f"{sid}: no slide title (titles should state the claim)")
    from .mathtext import unknown_commands
    for k, s in enumerate(slides, 1):                 # text that would render as raw LaTeX
        sid = s.get("id", f"#{k}")
        for t in _strings(s.get("content") or {}, skip=("latex", "image", "image_name", "number_name")) + \
                [z.get("text", "") for z in (s.get("zoom") or []) if isinstance(z, dict)]:
            if t.count("$") % 2:
                errs.append(f"{sid}: unbalanced $ in “{t[:60]}” – inline math must be $...$")
                continue
            outside = re.sub(r"\$[^$]*\$", "", t)
            if re.search(r"\\[A-Za-z]+", outside):
                errs.append(f"{sid}: LaTeX outside $...$ in “{t[:60]}” – wrap the math in $...$")
            bad = unknown_commands(t)
            if bad:
                errs.append(f"{sid}: inline math uses unsupported commands {', '.join('\\' + b for b in bad)} in "
                            f"“{t[:60]}” – use plain symbols or supported ones (Greek, \\frac, \\sqrt, \\sum, \\log, ...)")
    from .render import latex_error
    for k, s in enumerate(slides, 1):                 # display formulas must be drawable
        if s.get("type") != "formula":
            continue
        c = s.get("content") or {}
        sid = s.get("id", f"#{k}")
        items = [("latex", c.get("latex", ""))] + [("term", t[0]) for t in (c.get("terms") or [])
                                                  if isinstance(t, (list, tuple)) and t]
        for what, src in items:
            try:
                latex_error(src)
            except ValueError as e:
                msg = str(e).strip().splitlines()[-1] if str(e).strip() else "parse error"
                errs.append(f"{sid}: {what} '{str(src)[:50]}' is not valid mathtext ({msg[:90]}); write it without $")
        for t in c.get("terms") or []:
            if not (isinstance(t, (list, tuple)) and len(t) == 2):
                errs.append(f"{sid}: terms must be [symbol, explanation] pairs")
                break
    zooms = [s.get("id") for s in slides if s.get("zoom")]
    if len(zooms) > 1:
        warns.append(f"more than one zoom sequence ({', '.join(zooms)}); the brief allows one")
    return errs, warns


def import_spec(src: Path, dst_dir: Path) -> dict:
    """Copy the spec and every image it uses into the session (self-contained, relative paths)."""
    deck = json.loads(src.read_text(encoding="utf-8"))
    assets = dst_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for s in deck.get("slides", []):
        c = s.get("content") or {}
        if "image" in c:
            p = Path(c["image"])
            if not p.is_absolute():
                cand = [src.parent / p, src.parent.parent / p, Path.cwd() / p]
                p = next((q for q in cand if q.exists()), cand[0])
            if p.exists():
                tgt = assets / p.name
                if p.resolve() != tgt.resolve():
                    shutil.copy(p, tgt)
                c["image"] = f"assets/{p.name}"
    return deck


def resolve(deck: dict, base: Path) -> dict:
    """Absolute image paths for the renderer (works on a copy)."""
    deck = json.loads(json.dumps(deck))
    for s in deck["slides"]:
        c = s.get("content") or {}
        if "image" in c and not Path(c["image"]).is_absolute():
            c["image"] = str(base / c["image"])
    return deck


# Which spec slides a ★ preview shows. Each entry is a group of interchangeable types;
# the first matching spec slide of each group is used, in deck order.
PREVIEW_GROUPS = {
    "style": [["title"], ["diagram", "figure_text", "figure"], ["formula"], ["evidence"]],
    "brand": [["title"], ["diagram", "figure_text", "figure"], ["formula"], ["evidence"], ["statement"]],
    "visual": [["diagram"], ["comparison"], ["figure_text", "figure"], ["formula", "content"]],
    "motion": None,                                       # the whole deck
}


def pick(deck: dict, stage: str) -> dict:
    groups = PREVIEW_GROUPS[stage]
    if not groups:
        return deck
    chosen = []
    for g in groups:
        s = next((s for s in deck["slides"] if s["type"] in g and s not in chosen), None)
        if s:
            chosen.append(s)
    while len(chosen) < len(groups):                      # spec lacks some types: fill with other content slides
        s = next((s for s in deck["slides"] if s not in chosen and s["type"] not in ("section", "credits")), None)
        if not s:
            break
        chosen.append(s)
    order = {id(s): i for i, s in enumerate(deck["slides"])}
    return dict(deck, slides=sorted(chosen, key=lambda s: order[id(s)]))
