"""★ options written by the design agents (JSON) -> Brand / VisualLanguage / Motion, with checks.

The agents (Claude, following the skill) write sessions/<s>/options/<stage>.json:

    {"stage": "style", "options": [ {"key": "A_...", "label": "...", "rationale": "...",
                                     "brand": {...}, "restraint": "balanced"}, ... ×4 ]}

    style  -> "brand": full Brand fields (colors, fonts, dark, motif ...)
    brand  -> "brand" (full) or "patch" (fields changed against the chosen style)
    visual -> "visual_language": VisualLanguage fields
    motion -> "motion": Motion fields

`validate()` enforces readability (contrast), real fonts (fetched from Google Fonts) and
diversity (four options must not be colour swaps of one design).
"""
from __future__ import annotations

import colorsys
import json
import math
from dataclasses import fields, replace
from pathlib import Path

from .brand import Brand, VisualLanguage, Motion
from . import fontfetch, fonts

STAGES = ("style", "brand", "visual", "motion")
HEX_FIELDS = ("bg", "surface", "text", "muted", "ours", "baseline", "highlight", "accent2")
MOTIFS = ("grid", "grid_sharp", "rings", "rule", "blocks", "none")
RESTRAINT = ("minimal", "balanced", "expressive")


# ------------------------------------------------------------------ colour maths
def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(h):
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _lab(h):
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(h))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b):
    return math.dist(_lab(a), _lab(b))


def hue(h):
    return colorsys.rgb_to_hls(*_rgb(h))[0] * 360


# ------------------------------------------------------------------ build objects
def _clean(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def brand_of(opt: dict, base: Brand | None = None) -> Brand:
    if "brand" in opt:
        d = dict(opt["brand"])
        d.setdefault("key", opt["key"])
        d.setdefault("label", opt.get("label", opt["key"]))
        for k in HEX_FIELDS:
            if k in d:
                d[k] = d[k].lstrip("#").upper()
        if base is not None:
            return replace(base, **_clean(Brand, d))
        return Brand(**_clean(Brand, d))
    if base is None:
        raise ValueError(f"{opt['key']}: needs a full 'brand'")
    patch = {k: (v.lstrip("#").upper() if k in HEX_FIELDS else v) for k, v in opt.get("patch", {}).items()}
    return replace(base, key=opt["key"], label=opt.get("label", opt["key"]), **_clean(Brand, patch))


def visual_of(opt: dict) -> VisualLanguage:
    d = dict(opt["visual_language"])
    d.setdefault("key", opt["key"])
    d.setdefault("label", opt.get("label", opt["key"]))
    return VisualLanguage(**_clean(VisualLanguage, d))


def motion_of(opt: dict) -> Motion:
    d = dict(opt["motion"])
    d.setdefault("key", opt["key"])
    d.setdefault("label", opt.get("label", opt["key"]))
    return Motion(**_clean(Motion, d))


# ------------------------------------------------------------------ validation
def _font_families(b: Brand):
    out = set()
    for f in (b.heading_font, b.body_font, b.mono_font):
        fam = f[:-len(" SemiBold")] if f.endswith(" SemiBold") else f
        out.add(fam)
    return out


def ensure_fonts(b: Brand) -> list[str]:
    errs = []
    for fam in sorted(_font_families(b)):
        if not fonts.known(fam):
            try:
                fontfetch.ensure_family(fam)
            except Exception as e:
                errs.append(f"font '{fam}': {e}")
    for f in (b.heading_font, b.body_font, b.mono_font):
        if not fonts.known(f):
            errs.append(f"font '{f}' not available (use a Google Fonts family name, optionally + ' SemiBold')")
    return errs


def check_brand(b: Brand) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for k in HEX_FIELDS:
        v = getattr(b, k)
        if not (isinstance(v, str) and len(v) == 6 and all(c in "0123456789ABCDEFabcdef" for c in v)):
            errs.append(f"{k}: '{v}' is not a 6-digit hex colour")
    if errs:
        return errs, warns
    rules = [("text", 7.0, 4.5), ("muted", 4.5, 3.0), ("ours", 3.0, 3.0), ("highlight", 3.0, 2.5)]
    for k, good, minimum in rules:
        c = contrast(getattr(b, k), b.bg)
        if c < minimum:
            errs.append(f"{k} on bg contrast {c:.1f} < {minimum}")
        elif c < good:
            warns.append(f"{k} on bg contrast {c:.1f} (aim ≥ {good}; projectors wash colours out)")
    if contrast(b.text, b.surface) < 4.5:
        errs.append(f"text on surface contrast {contrast(b.text, b.surface):.1f} < 4.5")
    if delta_e(b.ours, b.baseline) < 30:
        warns.append("ours vs baseline colours are close (ΔE < 30): the paper's method will not stand out")
    if b.dark != (luminance(b.bg) < 0.2):
        warns.append("'dark' flag does not match the background lightness")
    if b.motif not in MOTIFS and b.motif != "auto":
        errs.append(f"motif '{b.motif}' unknown ({', '.join(MOTIFS)})")
    return errs, warns


def _category(font: str) -> str:
    fam = font[:-len(" SemiBold")] if font.endswith(" SemiBold") else font
    return fontfetch.category(fam) or "?"


def validate(stage: str, data: dict, base: dict | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). base: {'brand': Brand, ...} of earlier choices."""
    errs, warns = [], []
    opts = data.get("options", [])
    if len(opts) != 4:
        errs.append(f"need exactly 4 options, got {len(opts)}")
    keys = [o.get("key") for o in opts]
    if len(set(keys)) != len(keys) or None in keys:
        errs.append("option keys must be unique and present")
    if errs:
        return errs, warns
    if stage in ("style", "brand"):
        brands = []
        for o in opts:
            try:
                b = brand_of(o, (base or {}).get("brand") if stage == "brand" else None)
            except Exception as e:
                errs.append(f"{o['key']}: {e}")
                continue
            e1 = ensure_fonts(b)
            e2, w2 = check_brand(b)
            errs += [f"{o['key']}: {m}" for m in e1 + e2]
            warns += [f"{o['key']}: {m}" for m in w2]
            if stage == "style" and o.get("restraint") not in RESTRAINT:
                errs.append(f"{o['key']}: 'restraint' must be one of {RESTRAINT}")
            brands.append((o["key"], b))
        if errs:
            return errs, warns
        for i in range(len(brands)):
            for j in range(i + 1, len(brands)):
                (ka, a), (kb, b) = brands[i], brands[j]
                same_type = _category(a.heading_font) == _category(b.heading_font) and a.heading_font == b.heading_font
                close = delta_e(a.ours, b.ours) < 25 and abs(luminance(a.bg) - luminance(b.bg)) < 0.1
                if close and same_type and a.motif == b.motif:
                    errs.append(f"{ka} and {kb} are near-duplicates (same fonts, motif, similar colours)")
        if stage == "style":
            dims = {
                "light/dark": {b.dark for _, b in brands},
                "heading font category": {_category(b.heading_font) for _, b in brands},
                "motif": {b.motif for _, b in brands},
                "accent hue family": {int(hue(b.ours) // 60) for _, b in brands},
            }
            spanned = [k for k, v in dims.items() if len(v) > 1]
            if len(spanned) < 3:
                errs.append("the 4 main styles must differ in at least 3 of: " + ", ".join(dims) +
                            f" (now only: {', '.join(spanned) or 'none'})")
        else:
            fonts_used = {b.heading_font for _, b in brands} | {b.body_font for _, b in brands}
            if len({b.ours for _, b in brands}) < 4:
                errs.append("each identity option needs its own 'ours' colour")
            if len(fonts_used) < 2:
                warns.append("all identity options use the same fonts: vary the type pairing in at least one")
    elif stage == "visual":
        sigs = []
        for o in opts:
            try:
                v = visual_of(o)
            except Exception as e:
                errs.append(f"{o['key']}: {e}")
                continue
            if v.corner not in ("round", "sharp", "pill"):
                errs.append(f"{o['key']}: corner must be round|sharp|pill")
            if v.arrow not in ("solid", "dashed", "none"):
                errs.append(f"{o['key']}: arrow must be solid|dashed|none")
            if v.fill_style not in ("tint", "outline", "solid"):
                errs.append(f"{o['key']}: fill_style must be tint|outline|solid")
            if v.depth not in ("flat", "shadow"):
                errs.append(f"{o['key']}: depth must be flat|shadow")
            sigs.append((o["key"], (v.corner, v.fill_style, v.arrow, v.depth)))
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                diff = sum(x != y for x, y in zip(sigs[i][1], sigs[j][1]))
                if diff < 2:
                    errs.append(f"{sigs[i][0]} and {sigs[j][0]} differ in fewer than 2 of corner/fill/arrow/depth")
    elif stage == "motion":
        modes = []
        for o in opts:
            try:
                m = motion_of(o)
            except Exception as e:
                errs.append(f"{o['key']}: {e}")
                continue
            if m.mode not in ("none", "basic", "local", "world"):
                errs.append(f"{o['key']}: mode must be none|basic|local|world")
            if not 600 <= m.morph_ms <= 2500:
                warns.append(f"{o['key']}: morph_ms {m.morph_ms} outside 600–2500 ms")
            modes.append(m.mode)
        if len(set(modes)) < 3:
            errs.append("motion options should cover at least 3 different modes")
    else:
        errs.append(f"unknown stage {stage}")
    return errs, warns


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
