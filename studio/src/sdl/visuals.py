"""Motifs, world backdrops and image sourcing (icons -> free photos -> generation).

Every external asset is appended to a credits log (outputs/<run>/credits.json).
"""
from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from pathlib import Path

from .brand import Brand
from .spec import El, W, H

ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- paper motif
def lowrank_motif(b: Brand, seed: int = 7, rank: int = 2, n: int = 6):
    """Motif for GeoRA-like papers: a weight matrix where r columns light up (low-rank update)."""
    def draw(x, y, w, h):
        rnd = random.Random(seed)
        cell = min(w, h) / n
        gap = cell * 0.16
        ox = x + (w - cell * n) / 2
        oy = y + (h - cell * n) / 2
        hot = set(rnd.sample(range(n), rank))
        els = []
        for r in range(n):
            for c in range(n):
                on = c in hot
                els.append(El("rect", ox + c * cell, oy + r * cell, cell - gap, cell - gap, role="motif",
                               style={"fill": b.ours if on else b.surface, "alpha": (0.35 + 0.65 * rnd.random()) if on else None,
                                      "corner": "sharp" if (b.motif == "grid_sharp" or b.key.startswith(("C_", "D_"))) else "round", "radius": cell * 0.18}))
        return els
    return draw


def style_motif(b: Brand):
    """The decorative signature of each main style (stage 0). `b.motif` picks it explicitly."""
    kind = b.motif
    if kind == "auto":
        kind = {"B_": "rings", "C_": "rule", "D_": "blocks"}.get(b.key[:2], "grid")
    if kind == "none":
        return None
    if kind == "rings":
        def draw(x, y, w, h):
            return [El("ellipse", x + w * 0.1, y + h * 0.1, w * 0.8, w * 0.8, role="motif",
                       style={"fill": "none", "line": b.ours, "line_w": 2.5}),
                    El("ellipse", x + w * 0.3, y + h * 0.3, w * 0.4, w * 0.4, role="motif",
                       style={"fill": b.highlight, "alpha": 0.25})]
        return draw
    if kind == "rule":
        def draw(x, y, w, h):
            line_y = y + h * 0.5
            gy = max(0.1, line_y - 1.25)          # keep the glyph on the slide near the top edge
            gh = min(1.2, line_y - gy)
            size = 54 if gh >= 1.2 else int(54 * gh / 1.2 * 0.85)   # shrink the glyph to the room left
            return [El("rect", x, line_y, w, 0.04, role="motif", style={"fill": b.text, "corner": "sharp"}),
                    El("text", x, gy, w, gh, role="motif", text=b.motif_glyph,
                       style={"font": "heading", "size": size, "color": "ours", "align": "r"})]
        return draw
    if kind == "blocks":
        def draw(x, y, w, h):
            return [El("rect", x, y, w, h * 0.55, role="motif", style={"fill": b.surface, "corner": "sharp"}),
                    El("ellipse", x + w * 0.45, y + h * 0.35, w * 0.55, w * 0.55, role="motif", style={"fill": b.ours})]
        return draw
    return lowrank_motif(b)


# ----------------------------------------------------------------- world backdrop
def world_backdrop(b: Brand, out: Path, scale: float = 2.2, seed: int = 3) -> Path:
    """A faint 'weight landscape': grid + a few singular directions. Large, low contrast."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    fig = plt.figure(figsize=(W * scale, H * scale), dpi=60)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W * scale)
    ax.set_ylim(0, H * scale)
    ax.axis("off")
    fig.patch.set_facecolor("#" + b.bg)
    grid = "#" + b.surface
    for gx in np.arange(0, W * scale, 0.6):
        ax.axvline(gx, color=grid, lw=1.2, zorder=0)
    for gy in np.arange(0, H * scale, 0.6):
        ax.axhline(gy, color=grid, lw=1.2, zorder=0)
    for k in range(5):
        x0, y0 = rng.uniform(0, W * scale), rng.uniform(0, H * scale)
        ang = rng.uniform(0, np.pi)
        L = rng.uniform(8, 20)
        ax.plot([x0 - L * np.cos(ang), x0 + L * np.cos(ang)], [y0 - L * np.sin(ang), y0 + L * np.sin(ang)],
                color="#" + (b.ours if k < 2 else b.baseline), lw=3 if k < 2 else 1.5, alpha=0.18, zorder=1)
    pts = rng.uniform([0, 0], [W * scale, H * scale], size=(140, 2))
    ax.scatter(pts[:, 0], pts[:, 1], s=rng.uniform(4, 30, 140), color="#" + b.ours, alpha=0.10, zorder=2)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


# ----------------------------------------------------------------- image sourcing
class Credits:
    def __init__(self, path: Path):
        self.path = path
        self.items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def add(self, **kw):
        if kw not in self.items:
            self.items.append(kw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=1, ensure_ascii=False), encoding="utf-8")


ICON_SOURCES = {
    "lucide": ("https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/{name}.svg", "ISC (Lucide)"),
    "material": ("https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/{name}/"
                 "materialsymbolsoutlined/{name}_24px.svg", "Apache-2.0 (Material Symbols)"),
}


def icon(name: str, color: str, out_dir: Path, credits: Credits, source="lucide", px=512) -> Path | None:
    """Icon library first (brief #6). SVG -> recoloured transparent PNG."""
    import cairosvg

    url, lic = ICON_SOURCES[source]
    url = url.format(name=name)
    out = out_dir / f"icon_{source}_{name}_{color}.png"
    if not out.exists():
        try:
            svg = urllib.request.urlopen(url, timeout=20).read().decode()
        except Exception:
            return None
        svg = svg.replace("currentColor", "#" + color)
        if source == "material":
            svg = svg.replace("<svg ", f'<svg fill="#{color}" ', 1)
        out_dir.mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=px, output_height=px)
    credits.add(kind="icon", name=name, source=source, url=url, license=lic)
    return out


def photo_search(query: str, provider: str = "wikimedia", limit: int = 5) -> list[dict]:
    """Free-licence photo search (2nd in the order). Needs network that the cloud sandbox lacks;
    run on the laptop. Unsplash/Pexels need API keys in env UNSPLASH_KEY / PEXELS_KEY."""
    import os

    if provider == "wikimedia":
        q = urllib.parse.urlencode({"action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
                                    "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
                                    "iiprop": "url|extmetadata", "format": "json"})
        req = urllib.request.Request(f"https://commons.wikimedia.org/w/api.php?{q}",
                                     headers={"User-Agent": "slide-design-lab/0.1"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        out = []
        for p in (data.get("query", {}).get("pages", {}) or {}).values():
            ii = p["imageinfo"][0]
            meta = ii.get("extmetadata", {})
            out.append({"url": ii["url"], "page": ii.get("descriptionurl"),
                        "license": meta.get("LicenseShortName", {}).get("value"),
                        "author": meta.get("Artist", {}).get("value")})
        return out
    if provider == "unsplash":
        key = os.environ["UNSPLASH_KEY"]
        q = urllib.parse.urlencode({"query": query, "per_page": limit})
        req = urllib.request.Request(f"https://api.unsplash.com/search/photos?{q}",
                                     headers={"Authorization": f"Client-ID {key}"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return [{"url": r["urls"]["regular"], "page": r["links"]["html"], "license": "Unsplash License",
                 "author": r["user"]["name"]} for r in data["results"]]
    if provider == "pexels":
        key = os.environ["PEXELS_KEY"]
        q = urllib.parse.urlencode({"query": query, "per_page": limit})
        req = urllib.request.Request(f"https://api.pexels.com/v1/search?{q}", headers={"Authorization": key})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return [{"url": r["src"]["large"], "page": r["url"], "license": "Pexels License",
                 "author": r["photographer"]} for r in data["photos"]]
    raise ValueError(provider)


def paper_figure(pdf: Path, page: int, bbox_frac: tuple, out: Path, credits: Credits, dpi: int = 300,
                 cite: str = "") -> Path:
    """Crop a figure from the paper PDF (the paper's own figures are the primary visual source)."""
    import pymupdf

    doc = pymupdf.open(str(pdf))
    pg = doc[page - 1]
    r = pg.rect
    x0, y0, x1, y1 = bbox_frac
    clip = pymupdf.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height, r.x0 + x1 * r.width, r.y0 + y1 * r.height)
    pix = pg.get_pixmap(dpi=dpi, clip=clip)
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out))
    credits.add(kind="paper-figure", file=out.name, source=cite or pdf.name, page=page)
    return out
