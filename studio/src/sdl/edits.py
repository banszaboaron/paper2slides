"""Polish step: free design edits on the built slides (positions, sizes, styles, hiding, image effects,
extra elements, background) and deck-wide brand patches. Stored in spec/overrides.json:

    {"<slide id>": {"edit": {"set": [...], "add": [...], "bg": "RRGGBB"}}, "_brand": {...}}

They are applied after the Assembler has laid a slide out, so they win over templates and tokens.
"""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

from .spec import El, Slide, W, H

COLORS = ("bg", "surface", "text", "muted", "ours", "baseline", "highlight", "accent2")
STYLE_KEYS = {"size", "bold", "color", "fill", "font", "align", "anchor", "alpha", "corner", "radius", "line",
              "line_w", "dash", "spacing", "pad", "vpad", "tracking", "shadow", "crop"}
KINDS = {"text", "rect", "ellipse", "line", "image"}


# ------------------------------------------------------------------ describe (for the agent)
def refs(slide: Slide) -> list[str]:
    names = [e.name for e in slide.elements]
    return [n if n and names.count(n) == 1 else f"#{k}" for k, n in enumerate(names)]


def describe(slide: Slide) -> list[dict]:
    out = []
    for ref, e in zip(refs(slide), slide.elements):
        d = {"ref": ref, "kind": e.kind, "role": e.role, "x": round(e.x, 2), "y": round(e.y, 2),
             "w": round(e.w, 2), "h": round(e.h, 2)}
        if e.text:
            d["text"] = e.text[:90]
        st = {k: v for k, v in e.style.items() if k in STYLE_KEYS and not isinstance(v, (dict, list))}
        if st:
            d["style"] = st
        if e.kind == "image":
            d["image"] = Path(str(e.data)).name
        out.append(d)
    return out


# ------------------------------------------------------------------ apply
def _image_fx(path: str, blur: float = 0, opacity: float = 1) -> str:
    from PIL import Image, ImageFilter
    src = Path(path)
    key = hashlib.md5(f"{src}|{blur}|{opacity}".encode()).hexdigest()[:10]
    out = src.parent / "_fx" / f"{src.stem}_{key}.png"
    if out.exists():
        return str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGBA")
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(float(blur)))
    if opacity < 1:
        a = im.getchannel("A").point(lambda v: int(v * max(0.0, float(opacity))))
        im.putalpha(a)
    im.save(out)
    return str(out)


def apply(slide: Slide, edit: dict, brand=None) -> list[str]:
    """Apply one slide's edit in place. -> problems (unknown refs etc.)."""
    probs = []
    if not edit:
        return probs
    if edit.get("bg"):
        slide.bg = str(edit["bg"]).lstrip("#")
    index = dict(zip(refs(slide), slide.elements))
    drop = set()
    for s in edit.get("set") or []:
        el = index.get(str(s.get("ref", "")))
        if el is None:
            probs.append(f"no element '{s.get('ref')}' on this slide (use a ref from the element list)")
            continue
        if s.get("hide"):
            drop.add(id(el))
            continue
        for k in ("x", "y", "w", "h"):
            if isinstance(s.get(k), (int, float)):
                setattr(el, k, float(s[k]))
        if isinstance(s.get("text"), str) and el.text:
            el.text = s["text"]
        st = {k: v for k, v in (s.get("style") or {}).items() if k in STYLE_KEYS}
        el.style = {**el.style, **st}
        if el.kind == "image" and (s.get("blur") or s.get("opacity") is not None):
            try:
                el.data = _image_fx(str(el.data), float(s.get("blur") or 0), float(s.get("opacity", 1)))
            except Exception as e:  # noqa: BLE001
                probs.append(f"{s.get('ref')}: image effect failed ({e})")
        elif s.get("opacity") is not None:
            el.style["alpha"] = float(s["opacity"])
    if drop:
        slide.elements = [e for e in slide.elements if id(e) not in drop]
    for k, a in enumerate(edit.get("add") or []):
        kind = a.get("kind")
        if kind not in KINDS:
            probs.append(f"add: kind must be one of {sorted(KINDS)}")
            continue
        st = {kk: v for kk, v in (a.get("style") or {}).items() if kk in STYLE_KEYS}
        if kind == "text":
            st = {"font": "body", "size": (brand.sizes["body"] if brand else 20), **st}
        data = None
        if kind == "image":
            data = a.get("image")
            if not data or not Path(data).exists():
                probs.append(f"add: image '{data}' not found (use a figure path from the list)")
                continue
        el = El(kind, float(a.get("x", 1)), float(a.get("y", 1)), float(a.get("w", 3)), float(a.get("h", 1)),
                name=f"!!added{k}", text=str(a.get("text") or ""), style=st, role="content", data=data,
                meaning=str(a.get("meaning") or ("added image" if kind == "image" else "")))
        (slide.elements.insert(0, el) if a.get("behind") else slide.elements.append(el))
    return probs


def patch_brand(b, patch: dict | None):
    """Deck-wide token patch: colours, fonts, sizes, margin, title rule."""
    if not patch:
        return b
    kw = {}
    for k in COLORS:
        if isinstance(patch.get(k), str):
            kw[k] = patch[k].lstrip("#").upper()
    for k in ("heading_font", "body_font", "mono_font"):
        if isinstance(patch.get(k), str):
            kw[k] = patch[k]
    for k in ("heading_bold", "title_rule", "dark"):
        if isinstance(patch.get(k), bool):
            kw[k] = patch[k]
    if isinstance(patch.get("margin"), (int, float)):
        kw["margin"] = float(patch["margin"])
    if isinstance(patch.get("sizes"), dict):
        kw["sizes"] = {**b.sizes, **{k: v for k, v in patch["sizes"].items() if isinstance(v, (int, float))}}
    return dataclasses.replace(b, **kw)


def brand_summary(b) -> dict:
    return {"colors": {k: getattr(b, k) for k in COLORS}, "heading_font": b.heading_font, "body_font": b.body_font,
            "mono_font": b.mono_font, "heading_bold": b.heading_bold, "sizes": b.sizes, "margin": b.margin,
            "title_rule": b.title_rule, "canvas_inches": [W, H]}
