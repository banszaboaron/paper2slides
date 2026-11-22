"""Motion modes, chrome (progress bar, section marker), local zoom, world-Morph and
Morph frame simulation for cloud previews."""
from __future__ import annotations

import copy
from pathlib import Path

from .brand import Brand, Motion
from .spec import El, Slide, W, H


# ----------------------------------------------------------------- chrome
def chrome(b: Brand, idx: int, total: int, section_no: int, section: str, show_section=True,
           style: str = "bar", sections: list[str] | None = None) -> list[El]:
    """Navigation chrome. Every style keeps its moving part under one '!!' name so Morph slides it."""
    sections = sections or []
    page = El("text", W - b.margin - 1.0, H - 0.5, 1.0, 0.3, role="chrome", text=f"{idx}",
              style={"font": "mono", "size": b.sizes["chrome"], "color": "muted", "align": "r"})
    label = El("text", b.margin, 0.22, 7.0, 0.3, role="chrome", name="!!section",
               text=f"{section_no:02d} · {section.upper()}",
               style={"font": "mono", "size": b.sizes["chrome"], "color": "muted", "tracking": 1.2})
    if style == "none":
        return [page]
    if style == "bar":
        els = [El("rect", 0, H - 0.09, W, 0.09, role="chrome", name="!!track", style={"fill": b.surface, "corner": "sharp"}),
               El("rect", 0, H - 0.09, max(0.05, W * idx / total), 0.09, role="chrome", name="!!progress",
                  style={"fill": b.ours, "corner": "sharp"}), page]
        return els + ([label] if show_section and section else [])
    if style == "rail":                               # left rail that fills up
        els = [El("rect", 0.16, 0.5, 0.05, H - 1.0, role="chrome", name="!!track", style={"fill": b.surface, "corner": "sharp"}),
               El("rect", 0.16, 0.5, 0.05, max(0.05, (H - 1.0) * idx / total), role="chrome", name="!!progress",
                  style={"fill": b.ours, "corner": "sharp"}), page]
        return els + ([label] if show_section and section else [])
    if style == "dots":                               # one dot per section on the right edge, the current one travels
        n = max(len(sections), 1)
        y0 = H / 2 - (n - 1) * 0.22
        els = [El("ellipse", W - 0.36, y0 + k * 0.44 - 0.05, 0.1, 0.1, role="chrome", style={"fill": b.baseline})
               for k in range(n)]
        if section_no:
            els.append(El("ellipse", W - 0.41, y0 + (section_no - 1) * 0.44 - 0.1, 0.2, 0.2, role="chrome",
                          name="!!navdot", style={"fill": b.ours}))
        return els + [page] + ([label] if show_section and section else [])
    if style == "tabs":                               # section names on top, the underline travels
        n = max(len(sections), 1)
        x0, tw = b.margin, (W - 2 * b.margin) / n
        els = []
        for k, name in enumerate(sections):
            maxc = max(4, int((tw - 0.2) * 72 / (b.sizes["chrome"] * 0.62)))
            name = name if len(name) <= maxc else name[:maxc - 1] + "…"
            els.append(El("text", x0 + k * tw, 0.16, tw - 0.1, 0.3, role="chrome", text=name,
                          style={"font": "mono", "size": b.sizes["chrome"], "color": "text" if k + 1 == section_no else "muted",
                                 "label": True}))
        els.append(El("rect", x0, 0.5, W - 2 * b.margin, 0.015, role="chrome", name="!!tabtrack",
                      style={"fill": b.surface, "corner": "sharp"}))
        if section_no:
            els.append(El("rect", x0 + (section_no - 1) * tw, 0.48, tw - 0.3, 0.05, role="chrome", name="!!tabline",
                          style={"fill": b.ours, "corner": "sharp"}))
        return els + [page]
    return [page]


# ----------------------------------------------------------------- local zoom (crop-based)
def snap_region(path, region, max_grow: float = 0.12, ink_share: float = 0.015):
    """Move the edges of a zoom region (fx0, fy0, fx1, fy1) until no edge cuts through ink (text, axes, markers).

    The agent picks the region by eye from a downscaled image; its edges often slice a word of an axis
    label or a legend ("jority-voting accuracy"). Each edge that lies on ink moves to the nearest (almost)
    background line within `max_grow` of the image, outward or inward (outward wins a tie)."""
    from PIL import Image
    im = Image.open(path).convert("L")
    w, h = im.size
    px = im.load()
    hist = im.histogram()
    bg = max(range(256), key=lambda v: hist[v])               # the dominant tone is the background

    def inked(xs, ys):
        n = bad = 0
        for x in xs:
            for y in ys:
                n += 1
                bad += abs(px[x, y] - bg) > 48
        return n and bad / n > ink_share

    x0, y0 = int(region[0] * w), int(region[1] * h)
    x1, y1 = min(w - 1, int(region[2] * w)), min(h - 1, int(region[3] * h))
    gx, gy = int(max_grow * w), int(max_grow * h)

    def nearest_clean(pos, lo, hi, line, out_dir):
        """The clean line nearest to pos within [lo, hi] (ties: outward); pos itself if it is clean or lies
        on the image border (nothing is cut there)."""
        if pos <= 0 or pos >= (w if line is col else h) - 1 or not line(pos):
            return pos
        for d in range(1, max(pos - lo, hi - pos) + 1):
            for q in (pos + out_dir * d, pos - out_dir * d):
                if lo <= q <= hi and not line(q):
                    return q
        return pos

    col = lambda x: inked([x], range(y0, y1 + 1))
    x0 = nearest_clean(x0, max(0, x0 - gx), min(x1 - 1, x0 + gx), col, -1)
    x1 = nearest_clean(x1, max(x0 + 1, x1 - gx), min(w - 1, x1 + gx), col, +1)
    row = lambda y: inked(range(x0, x1 + 1), [y])
    y0 = nearest_clean(y0, max(0, y0 - gy), min(y1 - 1, y0 + gy), row, -1)
    y1 = nearest_clean(y1, max(y0 + 1, y1 - gy), min(h - 1, y1 + gy), row, +1)
    return (x0 / w, y0 / h, (x1 + 1) / w, (y1 + 1) / h)


def zoom_crop(region, box_w, box_h, img_ar, zoom_max=3.0):
    """Crop fractions (l, t, r, b) that show `region` (fx0, fy0, fx1, fy1) in a box of the
    given aspect without distortion, limited to zoom_max."""
    fx0, fy0, fx1, fy1 = region
    cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    rw, rh = fx1 - fx0, fy1 - fy0
    box_ar = box_w / box_h
    # region aspect in image units -> match the box aspect
    if (rw * img_ar) / rh > box_ar:
        rh = rw * img_ar / box_ar
    else:
        rw = rh * box_ar / img_ar
    rw = max(rw, 1 / zoom_max)
    rh = max(rh, 1 / zoom_max)
    rw, rh = min(rw, 1), min(rh, 1)
    x0 = min(max(cx - rw / 2, 0), 1 - rw)
    y0 = min(max(cy - rh / 2, 0), 1 - rh)
    return (x0, y0, 1 - (x0 + rw), 1 - (y0 + rh))


# ----------------------------------------------------------------- world-Morph
def world_element(path: str, section_no: int, n_sections: int, scale: float = 2.2) -> El:
    """One huge background picture; the 'camera' moves along a diagonal path per section."""
    ww, wh = W * scale, H * scale
    t = 0 if n_sections <= 1 else (section_no - 1) / (n_sections - 1)
    x = -(ww - W) * t
    y = -(wh - H) * (0.5 - 0.5 * __import__("math").cos(t * 3.14159))
    return El("image", x, y, ww, wh, name="!!world", role="world", data=path,
              meaning="world-Morph backdrop (exception: unifies the deck)")


def apply_motion(slides: list[Slide], m: Motion):
    plan = (getattr(m, "plan", None) or {}).get("transitions", {})
    static_titles = getattr(m, "title_flow", "carry") == "static"
    for i, s in enumerate(slides):
        if m.mode == "none":
            s.transition = "fade"
            for e in s.elements:
                if e.name.startswith("!!"):
                    e.name = e.name[2:]
            continue
        if static_titles:                             # titles fade in place instead of gliding
            for e in s.elements:
                if e.name == "!!title":
                    e.name = f"title{i}"
        if i == 0:
            s.transition = "fade"
            continue
        sid = str(s.meta.get("id", "")).split(".")[0]
        s.transition = plan.get(sid) or s.meta.get("transition", "morph")
        if s.transition not in ("morph", "fade"):
            s.transition = "morph"
    return slides


# ----------------------------------------------------------------- Morph frame simulation
def _lerp(a, b, t):
    return a + (b - a) * t


def interpolate(a: Slide, b: Slide, t: float) -> Slide:
    """Approximate a Morph frame between two physical slides (for cloud previews only)."""
    out = Slide(b.kind, bg=b.bg if t >= 0.5 else a.bg, transition="none")
    an = {e.name: e for e in a.elements if e.name}
    bn = {e.name: e for e in b.elements if e.name}
    for e in a.elements:
        if e.name in bn:
            continue
        if t < 0.5:
            out.elements.append(_ghost(e))
    for e in b.elements:
        if e.name and e.name in an:
            s = an[e.name]
            ne = copy.deepcopy(e if t >= 0.5 else s)
            ne.x, ne.y, ne.w, ne.h = (_lerp(s.x, e.x, t), _lerp(s.y, e.y, t), _lerp(s.w, e.w, t), _lerp(s.h, e.h, t))
            ca, cb = s.style.get("crop"), e.style.get("crop")
            if ca or cb:
                ca, cb = ca or (0, 0, 0, 0), cb or (0, 0, 0, 0)
                ne.style = dict(ne.style, crop=tuple(_lerp(p, q, t) for p, q in zip(ca, cb)))
            ne.build = None
            out.elements.append(ne)
        elif t >= 0.5:
            out.elements.append(_ghost(e))
    return out


def _ghost(e: El) -> El:
    g = copy.deepcopy(e)
    g.build = None
    return g


def morph_frames(slides: list[Slide], pairs: list[tuple[int, int]], steps=(0.0, 0.25, 0.5, 0.75, 1.0)):
    frames = []
    for a, b in pairs:
        for t in steps:
            frames.append(interpolate(slides[a], slides[b], t))
    return frames
