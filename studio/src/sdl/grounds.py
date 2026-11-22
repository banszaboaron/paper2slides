"""Slide grounds: what is behind the content (the deck's 'paper').

One picture per (brand, kind, strength), generated in the brand colours and cached. Content slides get
the quiet version, title / section / statement / closing slides the stronger one, so the ground gives
the deck a character without ever competing with the text.

kinds: solid | gradient | radial | grain | dots | lines | topo | mesh | paper
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .brand import Brand
from .spec import El, W, H

KINDS = ("solid", "gradient", "radial", "grain", "dots", "lines", "topo", "mesh", "paper")
LOUD = ("title", "section", "statement", "closing")
PX = (1600, 900)
CACHE = Path(__file__).resolve().parents[2] / "outputs" / "_cache" / "grounds"


def _rgb(h: str):
    import numpy as np
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def _blur(a, r):
    from PIL import Image, ImageFilter
    import numpy as np
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype("uint8"))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), float) / 255


def picture(b: Brand, kind: str, loud: bool, seed: int = 3) -> str | None:
    if kind not in KINDS or kind == "solid":
        return None
    import numpy as np
    from PIL import Image
    key = hashlib.md5(json.dumps([b.bg, b.surface, b.text, b.ours, b.accent2, b.highlight, kind, loud, seed]).encode()
                      ).hexdigest()[:12]
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"ground_{kind}_{key}.jpg"
    if out.exists():
        return str(out)
    w, h = PX
    rng = np.random.default_rng(seed)
    bg, ours, text = _rgb(b.bg), _rgb(b.ours), _rgb(b.text)
    k = 1.0 if loud else 0.55                          # strength
    yy, xx = np.mgrid[0:h, 0:w]
    u, v = xx / w, yy / h
    img = np.ones((h, w, 3)) * bg

    def tint(mask, col, amount):
        nonlocal img
        img = img + (col - img) * (np.clip(mask, 0, 1)[..., None] * amount)

    if kind == "gradient":
        tint(np.clip((u * 0.6 + v * 0.4), 0, 1) ** 1.4, ours, 0.16 * k)
    elif kind == "radial":
        d = np.hypot(u - 0.92, (v - 0.08) * 0.56)
        tint(np.exp(-(d / 0.42) ** 2), ours, 0.26 * k)
        d2 = np.hypot(u - 0.05, (v - 1.0) * 0.56)
        tint(np.exp(-(d2 / 0.30) ** 2), _rgb(b.accent2), 0.10 * k)
    elif kind == "grain":
        n = rng.normal(0, 1, (h, w))
        img = img + (n[..., None] * (5.5 * k))
        vig = np.hypot(u - 0.5, (v - 0.5) * 0.56)
        tint(np.clip((vig - 0.25) / 0.4, 0, 1), text if not b.dark else np.zeros(3), 0.06 * k)
    elif kind == "dots":
        step = 32
        m = ((xx % step - step / 2) ** 2 + (yy % step - step / 2) ** 2) <= 2.2 ** 2
        fade = 1 - 0.65 * u if not loud else 1.0
        tint(m * fade, text, 0.13 * k + 0.03)
    elif kind == "lines":
        step = 48
        m = ((xx % step) < 1.4) | ((yy % step) < 1.4)
        m2 = ((xx % (step * 4)) < 2.2) | ((yy % (step * 4)) < 2.2)
        tint(m * 1.0, text, 0.06 * k + 0.02)
        tint(m2 * 1.0, ours, 0.10 * k)
    elif kind == "topo":                               # contour lines of a smooth height field
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        f = np.zeros((h // 4, w // 4))
        uu, vv = u[::4, ::4], v[::4, ::4]
        for o in range(4):
            kx, ky = rng.normal(size=2) * (0.9 * 1.6 ** o)
            f += np.sin(uu * kx * 6.28 + vv * ky * 6.28 + rng.uniform(0, 6.28)) / (1.5 ** o)
        if not loud:
            f = f * (0.4 + 0.6 * uu)
        fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        col = (bg + (ours - bg) * (0.30 * k + 0.08)) / 255
        ax.imshow(np.ones((2, 2, 3)) * bg / 255, extent=(0, 1, 1, 0), aspect="auto")
        ax.contour(uu, vv, f, levels=12, colors=[col], linewidths=1.1, linestyles="solid")
        ax.set_xlim(0, 1)
        ax.set_ylim(1, 0)
        fig.canvas.draw()
        img = np.asarray(fig.canvas.buffer_rgba())[..., :3].astype(float)
        plt.close(fig)
    elif kind == "mesh":
        for col, (cx, cy, rad, a) in zip((ours, _rgb(b.accent2), _rgb(b.highlight)),
                                          ((0.85, 0.15, 0.40, 0.30), (0.10, 0.95, 0.35, 0.18), (0.55, 0.65, 0.25, 0.10))):
            d = np.hypot(u - cx, (v - cy) * 0.56)
            tint(np.exp(-(d / rad) ** 2), col, a * k)
    elif kind == "paper":
        n = _blur(rng.uniform(0, 1, (h, w)), 1.2)
        fib = _blur((rng.uniform(0, 1, (h, w)) > 0.995) * 1.0, 0.6)
        img = img + ((n - 0.5) * 16 * k)[..., None] - (fib * 30 * k)[..., None]
        warm = np.array([255, 240, 214.0]) if not b.dark else bg
        tint(np.ones((h, w)) * 1.0, warm, 0.05 * k)
    Image.fromarray(np.clip(img, 0, 255).astype("uint8")).save(out, quality=90)
    return str(out)


def ground_el(b: Brand, kind: str, slide_kind: str) -> El | None:
    p = picture(b, kind, slide_kind in LOUD)
    if not p:
        return None
    return El("image", 0, 0, W, H, name="!!ground", role="world", data=p,
              meaning="slide ground (texture/colour field behind the content)", style={"allow_offslide": True})


def contrast_on(fill: str) -> str:
    h = fill.lstrip("#")
    r, g, bl = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * bl
    return "111111" if lum > 0.55 else "FFFFFF"


def recolour_on(els: list[El], fill: str):
    """Section slides on a coloured field: every text and line takes the contrast colour."""
    c = contrast_on(fill)
    for e in els:
        if e.role in ("world",):
            continue
        if e.kind in ("text", "rect", "ellipse") and e.text:
            e.style = dict(e.style, color=c, accent=c)
        if e.kind in ("rect", "ellipse") and e.style.get("fill") in ("ours",):
            e.style = dict(e.style, fill=c)
        if e.kind == "line":
            e.style = dict(e.style, color=c)
