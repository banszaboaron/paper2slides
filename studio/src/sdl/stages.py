"""The ★ design stages (v2 order) and how their choices compose into the deck tokens.

    palette  -> colours with semantic roles            (board: swatches, contrast, mini chart)
    type     -> heading / body / mono fonts + scale    (board: type specimen)
    graphics -> charts, tables, figure frames          (board: sample chart, table, figure)
    visual   -> shape language (VisualLanguage)        (board: shapes, text boxes, arrows)
    layout   -> composition: layouts, motif, backdrop  (preview: lorem-ipsum slides, PowerPoint render)
    motion   -> Morph mode, world-Morph world          (preview: the team's real deck, PowerPoint video)

Each option carries its own section (`"palette": {...}`, `"type": {...}` ...). A session's
tokens are the chosen sections; `compose()` turns them into one Brand.
"""
from __future__ import annotations

import json
from dataclasses import fields

from .brand import Brand, VisualLanguage, Motion, DEFAULT_GRAPHICS, json_copy
from . import options as opt
from . import world as wd

STAGES = ["palette", "type", "graphics", "visual", "layout", "motion"]
NEEDS = {"type": "palette", "graphics": "type", "visual": "graphics", "layout": "visual", "motion": "layout"}
SECTION = {"palette": "palette", "type": "type", "graphics": "graphics", "visual": "visual_language",
           "layout": "layout", "motion": "motion"}
LABELS_HU = {"palette": "Palette", "type": "Type", "graphics": "Graphics", "visual": "Shapes",
             "layout": "Main style", "motion": "Deck"}

PALETTE_KEYS = ("bg", "surface", "text", "muted", "ours", "baseline", "highlight", "accent2")
SCALES = {
    "compact": {"display": 48, "title": 30, "h2": 22, "body": 18, "small": 15, "caption": 12, "big": 108, "chrome": 11},
    "standard": {"display": 54, "title": 34, "h2": 24, "body": 20, "small": 16, "caption": 13, "big": 120, "chrome": 11},
    "large": {"display": 60, "title": 38, "h2": 26, "body": 22, "small": 17, "caption": 14, "big": 132, "chrome": 12},
}
LAYOUT_CHOICES = {
    "title": ("title_left", "title_center"), "section": ("section_number", "section_center"),
    "statement": ("statement_sentence", "statement_number"), "closing": ("closing_takeaways", "closing_thanks"),
}
BACKDROPS = ("plain", "band_left", "band_top", "corner")

DEFAULT = Brand(key="default", label="Default", bg="FAFAF8", surface="EEEFF2", text="15171C", muted="5E6470",
                ours="2348D8", baseline="A2A8B2", highlight="C2410C", accent2="0E7490",
                heading_font="Inter SemiBold", body_font="Inter", mono_font="JetBrains Mono", heading_bold=False)


# ------------------------------------------------------------------ composition
def compose(t: dict) -> Brand:
    """tokens {'palette': {...}, 'type': {...}, 'graphics': {...}, 'layout': {...}} -> Brand."""
    d = DEFAULT.to_dict()
    pal = t.get("palette") or {}
    for k in PALETTE_KEYS:
        if k in pal:
            d[k] = str(pal[k]).lstrip("#").upper()
    if "dark" in pal:
        d["dark"] = bool(pal["dark"])
    else:
        d["dark"] = opt.luminance(d["bg"]) < 0.2
    ty = t.get("type") or {}
    for k in ("heading_font", "body_font", "mono_font", "heading_bold"):
        if k in ty:
            d[k] = ty[k]
    d["sizes"] = json_copy(SCALES.get(ty.get("scale", "standard"), SCALES["standard"]))
    g = json_copy(DEFAULT_GRAPHICS)
    for k, v in (t.get("graphics") or {}).items():
        if isinstance(v, dict):
            g.setdefault(k, {}).update(v)
    d["graphics"] = g
    lay = t.get("layout") or {}
    d["layouts"] = {k: v for k, v in (lay.get("layouts") or {}).items() if v in LAYOUT_CHOICES.get(k, ())}
    for k in ("motif", "motif_glyph", "backdrop", "title_rule", "margin"):
        if k in lay:
            d[k] = lay[k]
    if "motif" not in lay:
        d["motif"] = "none"
    d["key"] = "+".join(str((t.get(s) or {}).get("_key", "")) for s in ("palette", "type", "layout")).strip("+") or "brand"
    d["label"] = " · ".join(str((t.get(s) or {}).get("_label", "")) for s in ("palette", "type") if t.get(s)) or "Default"
    names = {f.name for f in fields(Brand)}
    return Brand(**{k: v for k, v in d.items() if k in names})


def visual_of(t: dict) -> VisualLanguage:
    v = dict(t.get("visual_language") or {})
    v.setdefault("key", v.pop("_key", "default"))
    v.setdefault("label", v.pop("_label", "Default"))
    v = {k: x for k, x in v.items() if not k.startswith("_")}
    return VisualLanguage(**{k: x for k, x in v.items() if k in {f.name for f in fields(VisualLanguage)}})


def motion_of(t: dict) -> Motion:
    m = dict(t.get("motion") or {"mode": "basic"})
    m.setdefault("key", m.pop("_key", "default"))
    m.setdefault("label", m.pop("_label", "Basic Morph"))
    m = {k: x for k, x in m.items() if not k.startswith("_")}
    return Motion(**{k: x for k, x in m.items() if k in {f.name for f in fields(Motion)}})


def with_option(tokens: dict, stage: str, o: dict) -> dict:
    """Session tokens + one option of `stage` (what that option's preview shows)."""
    t = json.loads(json.dumps(tokens))
    sec = dict(o.get(SECTION[stage]) or {})
    sec["_key"], sec["_label"] = o["key"], o.get("label", o["key"])
    t[SECTION[stage]] = sec
    return t


def resolved(tokens: dict) -> dict:
    """Everything a board needs to draw an option in the browser."""
    b = compose(tokens)
    from .fonts import _registry_files  # noqa
    return {"brand": b.to_dict(), "visual_language": visual_of(tokens).__dict__ | {},
            "motion": motion_of(tokens).__dict__ | {}}


# ------------------------------------------------------------------ validation
def _pairs(items):
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


def validate(stage: str, data: dict, tokens: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    opts = data.get("options", [])
    if len(opts) != 4:
        return [f"need exactly 4 options, got {len(opts)}"], warns
    keys = [o.get("key") for o in opts]
    if len(set(keys)) != 4 or None in keys:
        return ["option keys must be unique and present"], warns
    sec = SECTION[stage]
    for o in opts:
        if not isinstance(o.get(sec), dict):
            errs.append(f"{o['key']}: missing '{sec}' object")
        if not o.get("label"):
            errs.append(f"{o['key']}: missing 'label'")
    if errs:
        return errs, warns

    if stage == "palette":
        brands = []
        for o in opts:
            p = o["palette"]
            miss = [k for k in PALETTE_KEYS if k not in p]
            if miss:
                errs.append(f"{o['key']}: palette is missing {', '.join(miss)}")
                continue
            b = compose(with_option(tokens, stage, o))
            e, w = opt.check_brand(b)
            errs += [f"{o['key']}: {m}" for m in e]
            warns += [f"{o['key']}: {m}" for m in w]
            brands.append((o["key"], b))
        if not errs:
            if len({b.ours for _, b in brands}) < 4:
                errs.append("each palette needs its own 'ours' accent")
            darks = {b.dark for _, b in brands}
            hues = {int(opt.hue(b.ours) // 45) for _, b in brands}
            if len(darks) < 2 and len(hues) < 3:
                errs.append("palettes are too similar: mix light and dark, or use at least 3 accent hue families")
            for (ka, a), (kb, b) in _pairs(brands):
                if opt.delta_e(a.ours, b.ours) < 18 and opt.delta_e(a.bg, b.bg) < 10:
                    errs.append(f"{ka} and {kb} are near-duplicates")
    elif stage == "type":
        heads = []
        for o in opts:
            ty = o["type"]
            for k in ("heading_font", "body_font", "mono_font"):
                if not ty.get(k):
                    errs.append(f"{o['key']}: missing {k}")
            if ty.get("scale", "standard") not in SCALES:
                errs.append(f"{o['key']}: scale must be one of {', '.join(SCALES)}")
            if errs:
                continue
            b = compose(with_option(tokens, stage, o))
            errs += [f"{o['key']}: {m}" for m in opt.ensure_fonts(b)]
            heads.append(b.heading_font)
        if not errs:
            if len(set(heads)) < 4:
                errs.append("each option needs its own heading font")
            cats = {opt._category(h) for h in heads}
            if len(cats) < 2:
                errs.append("use at least two heading-font categories (e.g. sans and serif)")
    elif stage == "graphics":
        sigs = []
        for o in opts:
            g = o["graphics"]
            c, t, f = g.get("chart", {}), g.get("table", {}), g.get("figure", {})
            if c.get("labels", "outside") not in ("outside", "inside", "none"):
                errs.append(f"{o['key']}: chart.labels must be outside|inside|none")
            if c.get("others", "baseline") not in ("baseline", "muted"):
                errs.append(f"{o['key']}: chart.others must be baseline|muted")
            if t.get("header", "line") not in ("line", "fill", "none"):
                errs.append(f"{o['key']}: table.header must be line|fill|none")
            if t.get("highlight", "tint") not in ("tint", "bar", "bold"):
                errs.append(f"{o['key']}: table.highlight must be tint|bar|bold")
            if f.get("frame", "none") not in ("none", "card", "border"):
                errs.append(f"{o['key']}: figure.frame must be none|card|border")
            sigs.append((o["key"], (c.get("labels"), bool(c.get("gridlines")), c.get("gap"), t.get("header"),
                                    t.get("highlight"), bool(t.get("stripes")), f.get("frame"), bool(f.get("shadow")))))
        for (ka, a), (kb, b) in _pairs(sigs):
            if sum(x != y for x, y in zip(a, b)) < 2:
                errs.append(f"{ka} and {kb} differ in fewer than 2 graphic choices")
    elif stage == "visual":
        e, w = opt.validate("visual", data, None)
        errs += e
        warns += w
    elif stage == "layout":
        sigs = []
        for o in opts:
            lay = o["layout"]
            if lay.get("motif", "none") not in opt.MOTIFS:
                errs.append(f"{o['key']}: motif must be one of {', '.join(opt.MOTIFS)}")
            if lay.get("backdrop", "plain") not in BACKDROPS:
                errs.append(f"{o['key']}: backdrop must be one of {', '.join(BACKDROPS)}")
            for k, v in (lay.get("layouts") or {}).items():
                if k in LAYOUT_CHOICES and v not in LAYOUT_CHOICES[k]:
                    errs.append(f"{o['key']}: layouts.{k} must be one of {', '.join(LAYOUT_CHOICES[k])}")
            ls = lay.get("layouts") or {}
            sigs.append((o["key"], (lay.get("motif"), lay.get("backdrop", "plain"), ls.get("title"), ls.get("section"),
                                    bool(lay.get("title_rule")))))
        for (ka, a), (kb, b) in _pairs(sigs):
            if sum(x != y for x, y in zip(a, b)) < 2:
                errs.append(f"{ka} and {kb} differ in fewer than 2 of motif / backdrop / title / section / title rule")
    elif stage == "motion":
        from . import canvas as cv, grounds as gr
        modes, grounds_, sections_ = [], [], []
        for o in opts:
            m = o["motion"]
            if m.get("mode") not in ("none", "basic", "local", "world"):
                errs.append(f"{o['key']}: mode must be none|basic|local|world")
            if m.get("mode") == "world":
                tpl = (m.get("world") or {}).get("template")
                if tpl is None:
                    warns.append(f"{o['key']}: no world.template given – the motion director picks one")
                elif tpl not in cv.KINDS:
                    errs.append(f"{o['key']}: world.template must be one of {', '.join(cv.KINDS)}")
            else:
                g = m.get("ground") or {}
                if g.get("kind", "solid") not in gr.KINDS:
                    errs.append(f"{o['key']}: ground.kind must be one of {', '.join(gr.KINDS)}")
                if g.get("section", "same") not in ("same", "surface", "ours", "text"):
                    errs.append(f"{o['key']}: ground.section must be same|surface|ours|text")
                grounds_.append(g.get("kind", "solid"))
                sections_.append(g.get("section", "same"))
            if not 600 <= int(m.get("morph_ms", 1200)) <= 2500:
                warns.append(f"{o['key']}: morph_ms outside 600–2500")
            modes.append(m.get("mode"))
        if modes.count("world") != 1:
            errs.append("exactly one option must be the canvas world (mode world)")
        if len(set(m for m in modes if m != "world")) < 2:
            errs.append("the three other options need at least 2 different modes (none / basic / local)")
        if len(set(grounds_)) < len(grounds_):
            errs.append("every non-world option needs its own ground kind – the decks must look different")
        if len(set(sections_)) < 2:
            errs.append("vary the section dividers (ground.section) across the options")
        chromes = [(o["motion"].get("chrome") or "bar") for o in opts if o["motion"].get("mode") != "world"]
        bad = [c for c in chromes if c not in ("bar", "dots", "tabs", "rail", "none")]
        if bad:
            errs.append(f"chrome must be bar|dots|tabs|rail|none (got {', '.join(bad)})")
        elif len(set(chromes)) < len(chromes):
            errs.append("every non-world option needs its own chrome style")
        for o in opts:
            if o["motion"].get("title_flow", "carry") not in ("carry", "static"):
                errs.append(f"{o['key']}: title_flow must be carry|static")
    return errs, warns
