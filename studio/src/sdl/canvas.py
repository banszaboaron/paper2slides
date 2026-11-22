"""Canvas world (Prezi-style zooming presentation) built from native PowerPoint Morph.

The whole talk lives on ONE big canvas:
  * a picture of a world (the art: a road, a head with a brain, a mountain, a neural network, an orbit
    system), drawn for this paper in the brand colours;
  * one topic circle per section, placed where the art gives it a meaning (a bend of the road, a branch
    of the mind map, a ledge on the mountain ...). The section number and name sit inside the circle;
  * every content slide is a frame inside its topic circle.

Every PowerPoint slide is a camera on that canvas: the title slide shows the whole world, a section slide
zooms to its circle (its frames visible as thumbnails), a content slide zooms into one frame. Each slide
carries the art, all circles, all frame cards and the content of the frames nearby, all at the same world
positions, so Morph moves the whole world together - the audience flies through the map.

Coordinates: world inches (WW x WH). A "transform" (x0, y0, s) maps slide inches to world inches:
world = (x0 + x * s, y0 + y * s). A camera is the same thing seen from the other side.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from .brand import Brand, VisualLanguage
from .spec import El, Slide, W, H

TEMPLATES = ("road", "mind", "climb", "network", "orbit")
KINDS = TEMPLATES + ("custom",)   # custom: the motion director draws the world itself (SVG)
SVG_W, SVG_H = 1600, 900          # the director's drawing units
WW, WH = W * 7.2, H * 7.2           # world size (inches): 96 x 54, same aspect as a slide
DPI = 100                           # art resolution: ~ screen resolution at topic zoom
CIRCLE_FILL = 0.92                  # circle diameter / slide height at topic zoom
MIN_PT = 2.2                        # text smaller than this on a slide is left out (the card shows)


# ------------------------------------------------------------------ colour helpers
def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(t):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in t)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))[1:]


def world_bg(b: Brand) -> str:
    """Canvas colour: one quiet step away from the slide background, so circles (bg) stand out."""
    return mix(b.bg, b.surface, 1.0) if b.surface.upper() != b.bg.upper() else mix(b.bg, b.text, 0.06)


# ------------------------------------------------------------------ geometry helpers
def _smooth(pts, n=400):
    """Catmull-Rom spline through pts -> dense polyline."""
    import numpy as np
    p = np.asarray(pts, float)
    p = np.vstack([p[0] - (p[1] - p[0]), p, p[-1] + (p[-1] - p[-2])])
    out = []
    seg = max(2, n // (len(p) - 3))
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, seg, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
                              (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(p[-2])
    return np.asarray(out)


def _clamp_c(x, y, r, pad=0.8):
    return (min(max(x, r + pad), WW - r - pad), min(max(y, r + pad), WH - r - pad))


def _radius(n: int) -> float:
    return max(3.4, min(5.6, 0.9 * WW / (2.4 * max(n, 1) + 1.0)))


# ------------------------------------------------------------------ the five worlds
def plan_world(template: str, n: int, seed: int = 7, custom: dict | None = None) -> dict:
    """Topic anchors (x, y, r), the title zone and everything the art needs, for n topics."""
    import numpy as np
    rng = np.random.default_rng(seed)
    r = _radius(n)
    if template == "custom" and custom and custom.get("svg") and len(custom.get("anchors") or []) >= n:
        k = WW / SVG_W
        anchors = _separate([_clamp_c(float(x) * k, float(y) * k, r) for x, y in custom["anchors"][:n]], r)
        tz = custom.get("title_zone") or [40, 40, 560, 260]
        return {"template": "custom", "r": r, "size": [WW, WH], "svg": custom["svg"],
                "anchors": [[float(x), float(y)] for x, y in anchors], "title_zone": [float(v) * k for v in tz]}
    t = template if template in TEMPLATES else "road"
    info: dict = {"template": t, "r": r, "size": [WW, WH]}
    if t == "road":
        # a ribbon road snaking from bottom-left to top-right; each topic sits on the outside of a bend
        k = max(n, 2)
        ts = np.linspace(0, 1, 9 * k)
        amp = WH * 0.12
        cx = WW * (0.06 + 0.84 * ts)
        cy = WH * (0.80 - 0.42 * ts) + amp * np.sin(math.pi * k * ts)
        road = np.c_[cx, cy]
        anchors = []
        for i in range(n):
            ti = (i + 0.5) / k
            x = WW * (0.06 + 0.84 * ti)
            y = WH * (0.80 - 0.42 * ti)
            side = math.sin(math.pi * k * ti)              # +1 / -1 at the bends
            y += amp * side
            best = None
            for sgn in (side, -side):                      # outside of the bend, unless the edge pushes it back on the road
                c = _clamp_c(x, y + sgn * (r + 2.0), r)
                d = float(np.min(np.hypot(road[:, 0] - c[0], road[:, 1] - c[1])))
                if best is None or d > best[0] + 0.5:
                    best = (d, c)
            anchors.append(best[1])
        info.update(road=road.tolist(), road_w=2.6)
        zones = [(WW * 0.03, WH * 0.04, WW * 0.34, WH * 0.26), (WW * 0.62, WH * 0.70, WW * 0.35, WH * 0.27),
                 (WW * 0.32, WH * 0.03, WW * 0.34, WH * 0.24)]
    elif t == "mind":
        # a head in profile with a brain; branches reach out to the topics on an arc around it
        hx, hy, hs = WW * 0.40, WH * 0.70, WH * 0.62     # head centre and size (height)
        info.update(head=[hx, hy, hs])
        a0, a1 = math.radians(172), math.radians(8)
        anchors = []
        for i in range(n):
            a = a0 + (a1 - a0) * (i / max(n - 1, 1))
            x = hx + 0.06 * WW + WW * 0.40 * math.cos(a)
            y = hy - 0.10 * WH - WH * 0.52 * math.sin(a)
            anchors.append(_clamp_c(x, y, r))
        zones = [(WW * 0.66, WH * 0.66, WW * 0.32, WH * 0.30), (WW * 0.02, WH * 0.68, WW * 0.26, WH * 0.30)]
    elif t == "climb":
        # a mountain; the path zig-zags up, the topics are ledges on the way, the last one is the summit
        peak = (WW * 0.62, WH * 0.16)
        base_l, base_r = (WW * 0.10, WH * 0.96), (WW * 0.98, WH * 0.96)
        info.update(peak=list(peak))
        anchors = []
        for i in range(n):
            f = (i + 0.6) / (n + 0.2)
            y = base_l[1] + (peak[1] - base_l[1]) * f
            half = (WW * 0.40) * (1 - f) + WW * 0.05
            x = peak[0] + (-1 if i % 2 == 0 else 1) * half * 0.62
            anchors.append(_clamp_c(x, y + r * 0.1, r))
        zones = [(WW * 0.02, WH * 0.04, WW * 0.34, WH * 0.30), (WW * 0.70, WH * 0.03, WW * 0.28, WH * 0.22)]
    elif t == "network":
        # a neural network seen from the side: layers left -> right; the topics are big neurons (forward pass)
        layers = max(n, 4) + 1
        xs = np.linspace(WW * 0.10, WW * 0.92, layers)
        nodes = []
        for li, x in enumerate(xs):
            m = 6 if li in (0, layers - 1) else 8
            ys = np.linspace(WH * 0.30, WH * 0.94, m) + rng.uniform(-0.6, 0.6, m)
            nodes.append([[float(x), float(y)] for y in ys])
        anchors = []
        for i in range(n):
            li = 1 + round(i * (layers - 2) / max(n - 1, 1)) if n > 1 else layers // 2
            y = WH * (0.52 + 0.20 * math.sin(i * 1.7 + 0.4))
            anchors.append(_clamp_c(float(xs[li]), y, r))
        info.update(layers=nodes)
        zones = [(WW * 0.02, WH * 0.03, WW * 0.40, WH * 0.22), (WW * 0.55, WH * 0.03, WW * 0.42, WH * 0.20)]
    else:  # orbit
        cx, cy = WW * 0.60, WH * 0.53
        info.update(core=[cx, cy])
        anchors = []
        for i in range(n):
            ring = 1 + i % 3
            rad = WW * (0.13 + 0.085 * ring)
            a = math.radians(160 - i * (300 / max(n, 1)))
            anchors.append(_clamp_c(cx + rad * math.cos(a), cy - 0.62 * rad * math.sin(a), r))
        zones = [(WW * 0.02, WH * 0.04, WW * 0.30, WH * 0.30), (WW * 0.02, WH * 0.66, WW * 0.30, WH * 0.30)]
    anchors = _separate(anchors, r)
    info["anchors"] = [[float(x), float(y)] for x, y in anchors]
    info["title_zone"] = _free_zone(zones, info["anchors"], r)
    return info


def _separate(pts, r, it=60):
    """Push overlapping circles apart (keeps the template's shape, avoids overlaps)."""
    pts = [list(p) for p in pts]
    for _ in range(it):
        moved = False
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
                d = math.hypot(dx, dy) or 0.01
                need = 2 * r + 1.4
                if d < need:
                    push = (need - d) / 2
                    ux, uy = dx / d, dy / d
                    pts[i][0] -= ux * push
                    pts[i][1] -= uy * push
                    pts[j][0] += ux * push
                    pts[j][1] += uy * push
                    moved = True
        pts = [list(_clamp_c(x, y, r)) for x, y in pts]
        if not moved:
            break
    return [tuple(p) for p in pts]


def _free_zone(zones, anchors, r):
    def cost(z):
        x, y, w, h = z
        c = 0.0
        for ax, ay in anchors:
            nx, ny = min(max(ax, x), x + w), min(max(ay, y), y + h)
            d = math.hypot(ax - nx, ay - ny)
            c += max(0.0, r + 0.8 - d)
        return c
    return list(min(zones, key=cost))


# ------------------------------------------------------------------ art
def _font(b: Brand, heading=True):
    from .world import _font_props
    return _font_props(b, heading)


def draw_art(b: Brand, info: dict, labels: list[str], core: str, out_dir: Path) -> str:
    """The world picture (raster, DPI ~ screen at topic zoom). Topic circles are NOT in it (native shapes)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Circle, Ellipse, Polygon, PathPatch
    from matplotlib.path import Path as MPath

    key = hashlib.md5(json.dumps([b.to_dict(), info, labels, core], sort_keys=True).encode()).hexdigest()[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"canvas_{info['template']}_{key}.png"
    if png.exists():
        return str(png)
    t = info["template"]
    bgc = world_bg(b)
    ink = lambda c: _hex(_rgb(c))                      # noqa: E731
    soft = mix(bgc, b.text, 0.10)                      # quiet structure
    soft2 = mix(bgc, b.text, 0.18)
    tint = mix(bgc, b.ours, 0.28)
    tint2 = mix(bgc, b.ours, 0.55)
    deep = mix(b.ours, b.text, 0.35)
    fig = plt.figure(figsize=(WW, WH), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, WW)
    ax.set_ylim(WH, 0)
    ax.axis("off")
    fig.patch.set_facecolor(ink(bgc))
    fh, fb = _font(b, True), _font(b, False)
    r = info["r"]
    anchors = info["anchors"]
    rng = np.random.default_rng(3)

    def lw(inches):                                    # line width in points for a width in world inches
        return inches * 72

    if t == "road":
        road = np.asarray(info["road"])
        sm = _smooth(road[::3], 700)
        w = info["road_w"]
        ax.plot(sm[:, 0] + 0.5, sm[:, 1] + 1.3, color=ink(mix(bgc, b.text, 0.12)), lw=lw(w * 1.05),
                solid_capstyle="round", zorder=1, alpha=0.55)                      # soft shadow
        ax.plot(sm[:, 0], sm[:, 1] + 0.55, color=ink(soft2), lw=lw(w), solid_capstyle="round", zorder=2)   # side
        ax.plot(sm[:, 0], sm[:, 1], color=ink(mix(bgc, "FFFFFF" if not b.dark else b.text, 0.65)),
                lw=lw(w * 0.96), solid_capstyle="round", zorder=3)                # top surface
        ax.plot(sm[:, 0], sm[:, 1], color=ink(tint2), lw=lw(0.07), ls=(0, (6, 6)), zorder=4)   # centre line
        for (x, y) in anchors:                         # a short spur from the road to every topic
            d = np.hypot(sm[:, 0] - x, sm[:, 1] - y)
            j = int(np.argmin(d))
            px, py = sm[j]
            ax.plot([px, x], [py, y], color=ink(tint2), lw=lw(0.12), zorder=4, solid_capstyle="round")
            ax.add_patch(Circle((px, py), 0.45, color=ink(b.ours), zorder=5))
        ax.add_patch(Circle(tuple(sm[0]), 0.9, color=ink(soft2), zorder=4))
        ax.add_patch(Circle(tuple(sm[-1]), 0.9, color=ink(b.ours), zorder=4))
    elif t == "mind":
        hx, hy, hs = info["head"]
        prof = np.array([(-0.25, -0.62), (-0.30, -0.42), (-0.42, -0.14), (-0.46, 0.14), (-0.40, 0.38),
                         (-0.24, 0.55), (0.0, 0.62), (0.20, 0.57), (0.34, 0.44), (0.40, 0.26), (0.41, 0.13),
                         (0.45, 0.05), (0.535, -0.08), (0.465, -0.12), (0.47, -0.17), (0.445, -0.21),
                         (0.465, -0.26), (0.43, -0.34), (0.36, -0.39), (0.24, -0.42), (0.16, -0.50),
                         (0.14, -0.70)])
        pts = _smooth(prof, 500)
        P = np.c_[hx + pts[:, 0] * hs, hy - pts[:, 1] * hs]
        P = np.vstack([P, [hx + 0.14 * hs, WH + 2], [hx - 0.25 * hs, WH + 2]])
        ax.add_patch(Polygon(P, closed=True, fc=ink(mix(bgc, b.text, 0.07)), ec=ink(soft2), lw=lw(0.12), zorder=2))
        bx, by, bw_, bh_ = hx - 0.02 * hs, hy - 0.30 * hs, 0.66 * hs, 0.48 * hs
        brain = Ellipse((bx, by), bw_, bh_, fc=ink(tint), ec=ink(b.ours), lw=lw(0.14), zorder=3)
        ax.add_patch(brain)
        for _ in range(26):                            # gyri: wandering curves clipped to the brain
            x0, y0 = bx + rng.uniform(-0.5, 0.5) * bw_, by + rng.uniform(-0.5, 0.5) * bh_
            ang = rng.uniform(0, 2 * math.pi)
            xs_, ys_ = [x0], [y0]
            for _s in range(28):
                ang += rng.normal(0, 0.55)
                xs_.append(xs_[-1] + 0.22 * hs * 0.06 * math.cos(ang) * 3)
                ys_.append(ys_[-1] + 0.22 * hs * 0.06 * math.sin(ang) * 3)
            ln, = ax.plot(xs_, ys_, color=ink(tint2), lw=lw(0.09), zorder=4, solid_capstyle="round")
            ln.set_clip_path(brain)
        ax.plot([bx, bx], [by - bh_ * 0.48, by + bh_ * 0.48], color=ink(b.ours), lw=lw(0.06), zorder=4, alpha=0.5)
        for (x, y) in anchors:                         # branches: from the brain's rim out to every topic
            a = math.atan2(y - by, x - bx)
            sx, sy = bx + 0.5 * bw_ * math.cos(a), by + 0.5 * bh_ * math.sin(a)
            mx, my = (sx + x) / 2 + 0.12 * (y - sy), (sy + y) / 2 - 0.12 * (x - sx)
            path = MPath([(sx, sy), (mx, my), (x, y)], [MPath.MOVETO, MPath.CURVE3, MPath.CURVE3])
            ax.add_patch(PathPatch(path, fc="none", ec=ink(b.ours), lw=lw(0.16), zorder=1, capstyle="round"))
            ax.add_patch(Circle((sx, sy), 0.32, color=ink(b.ours), zorder=5))
        if core:
            ax.text(bx, by, core, fontproperties=fh, fontsize=int(0.11 * hs * 72), color=ink(deep),
                    ha="center", va="center", zorder=6)
    elif t == "climb":
        px_, py_ = info["peak"]
        sky = np.linspace(0, 1, 2)
        far = np.array([(0, WH * 0.62), (WW * 0.16, WH * 0.40), (WW * 0.28, WH * 0.55), (WW * 0.40, WH * 0.34),
                        (WW * 0.52, WH * 0.50), (WW * 0.80, WH * 0.30), (WW, WH * 0.48), (WW, WH), (0, WH)])
        ax.add_patch(Polygon(far, closed=True, fc=ink(mix(bgc, b.text, 0.06)), ec="none", zorder=1))
        main = np.array([(WW * 0.04, WH), (WW * 0.30, WH * 0.62), (WW * 0.40, WH * 0.66), (px_, py_),
                         (WW * 0.74, WH * 0.40), (WW * 0.82, WH * 0.46), (WW * 1.02, WH * 0.90), (WW * 1.02, WH)])
        ax.add_patch(Polygon(main, closed=True, fc=ink(mix(bgc, b.text, 0.12)), ec="none", zorder=2))
        cap = np.array([(px_ - WW * 0.055, py_ + WH * 0.10), (px_, py_), (px_ + WW * 0.05, py_ + WH * 0.09),
                        (px_ + WW * 0.02, py_ + WH * 0.075), (px_ - WW * 0.01, py_ + WH * 0.10)])
        ax.add_patch(Polygon(cap, closed=True, fc=ink(mix(bgc, "FFFFFF" if not b.dark else b.text, 0.6)),
                             ec="none", zorder=3))
        trail = [(WW * 0.18, WH * 0.99)] + [tuple(a) for a in anchors]
        sm = _smooth(trail, 500)
        ax.plot(sm[:, 0], sm[:, 1], color=ink(b.ours), lw=lw(0.16), ls=(0, (3, 2)), zorder=4)
        fx, fy = anchors[-1]
        ax.plot([fx, fx], [fy - r - 0.2, fy - r - 3.4], color=ink(deep), lw=lw(0.12), zorder=5)
        ax.add_patch(Polygon([(fx, fy - r - 3.4), (fx + 2.2, fy - r - 2.8), (fx, fy - r - 2.2)], closed=True,
                             fc=ink(b.ours), ec="none", zorder=5))
        for hx in np.linspace(0, WW, 9):               # a few quiet hills in front
            ax.add_patch(Ellipse((hx + rng.uniform(-3, 3), WH * 1.02), WW * 0.22, WH * 0.12,
                                 fc=ink(mix(bgc, b.text, 0.16)), ec="none", zorder=3))
    elif t == "network":
        layers = info["layers"]
        for a, c in zip(layers, layers[1:]):
            for p in a:
                for q in c:
                    ax.plot([p[0], q[0]], [p[1], q[1]], color=ink(soft), lw=lw(0.035), zorder=1)
        for L in layers:
            for p in L:
                ax.add_patch(Circle(tuple(p), 0.55, fc=ink(bgc), ec=ink(soft2), lw=lw(0.08), zorder=2))
        for a, c in zip(anchors, anchors[1:]):          # the forward pass through the topics
            ax.plot([a[0], c[0]], [a[1], c[1]], color=ink(b.ours), lw=lw(0.22), zorder=1.5, solid_capstyle="round")
        for L in (layers[0], layers[-1]):
            for p in L:
                ax.add_patch(Circle(tuple(p), 0.55, fc=ink(tint), ec=ink(b.ours), lw=lw(0.08), zorder=3))
        if core:
            x, y = layers[-1][0]
            ax.text(x, y - 1.6, core, fontproperties=fh, fontsize=int(1.6 * 72), color=ink(deep), ha="center",
                    va="bottom", zorder=4)
    elif t == "orbit":
        cx, cy = info["core"]
        rings = sorted({round(math.hypot(x - cx, (y - cy) / 0.62), 2) for x, y in anchors})
        for rad in rings:
            ax.add_patch(Ellipse((cx, cy), 2 * rad, 2 * 0.62 * rad, fc="none", ec=ink(soft2), lw=lw(0.1), zorder=1))
        for k in range(5):
            ax.add_patch(Circle((cx, cy), 4.2 + k * 0.9, fc=ink(b.ours), alpha=0.08, ec="none", zorder=1))
        ax.add_patch(Circle((cx, cy), 4.0, fc=ink(b.ours), ec="none", zorder=2))
        for (x, y) in anchors:
            ax.plot([cx, x], [cy, y], color=ink(tint), lw=lw(0.06), zorder=1, ls=(0, (4, 4)))
        if core:
            ax.text(cx, cy, core, fontproperties=fh, fontsize=int(1.5 * 72), color=ink(b.bg), ha="center",
                    va="center", zorder=3)

    if t == "custom":
        draw_svg(ax, info["svg"], b, WW / SVG_W)
    for (x, y) in anchors:                             # a soft halo lifts every topic circle off the world
        ax.add_patch(Circle((x + 0.25, y + 0.45), r + 0.25, fc=ink(mix(bgc, "000000", 0.25)), ec="none",
                            zorder=7, alpha=0.35))
    fig.savefig(png, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(png)


# ------------------------------------------------------------------ transforms
def place(el: El, src: tuple, cam: tuple, b: Brand, vl: VisualLanguage) -> El | None:
    """An element authored in slide inches of frame `src`, seen through camera `cam`."""
    fx, fy, fs = src
    cx, cy, cs = cam
    k = fs / cs
    e = copy.deepcopy(el)
    e.x = (fx - cx) / cs + el.x * k
    e.y = (fy - cy) / cs + el.y * k
    e.w, e.h = el.w * k, el.h * k
    if abs(k - 1) < 1e-9 and src == cam:
        return e
    st = dict(e.style)
    if e.kind == "text" or (e.kind in ("rect", "ellipse") and e.text):
        st.setdefault("size", b.sizes["body"])
    if e.kind == "chart":
        st.setdefault("size", b.sizes["small"])
        st.setdefault("label_size", b.sizes["small"])
    if e.kind == "formula":
        st.setdefault("size", 30)
    if e.kind in ("rect", "ellipse") and st.get("line") not in (None, "none"):
        st.setdefault("line_w", vl.stroke_w)
    if e.kind in ("rect",) and st.get("corner", vl.corner) != "sharp":
        st.setdefault("radius", vl.radius)
    if e.kind == "line":
        st.setdefault("w", vl.stroke_w)
    for key in ("size", "label_size", "line_w", "w", "radius", "pad", "vpad", "para_gap", "tracking"):
        if isinstance(st.get(key), (int, float)):
            st[key] = st[key] * k
    st["_k"] = st.get("_k", 1.0) * k
    st["allow_offslide"] = True
    if st.get("size") is not None and st["size"] < MIN_PT and (e.kind == "text" or e.text):
        if e.kind == "text":
            return None
        e.text = ""
    if e.kind == "chart" and k < 0.3:
        return None
    if e.kind in ("rect", "ellipse") and st.get("line_w") is not None and st["line_w"] < 0.25:
        st["line_w"] = 0.25
    if e.kind == "line" and st.get("w", 1) < 0.25:
        st["w"] = 0.25
    far = max(abs(e.x), abs(e.y), abs(e.x + e.w), abs(e.y + e.h))
    if far > 1500:                                     # PowerPoint's coordinate limit is ~2000 in
        return None
    e.style = st
    return e


def world_el(kind: str, x, y, w, h, cam, **kw) -> El:
    """An element given in world inches, seen through camera `cam`."""
    cx, cy, cs = cam
    style = dict(kw.pop("style", {}))
    style["allow_offslide"] = True
    for key in ("line_w", "w", "size"):
        if key in style:
            style[key] = style[key] / cs
    return El(kind, (x - cx) / cs, (y - cy) / cs, w / cs, h / cs, style=style, role="world", **kw)


def overview_cam() -> tuple:
    return (0.0, 0.0, WW / W)


def topic_cam(x, y, r) -> tuple:
    s = 2 * r / (CIRCLE_FILL * H)
    return (x - (W / 2) * s, y - (H / 2) * s, s)


def frame_grid(x, y, r, k: int) -> list[tuple]:
    """k frames (16:9) inside the circle, below the topic title: transforms (x0, y0, s)."""
    if k <= 0:
        return []
    gx0, gx1 = x - 0.76 * r, x + 0.76 * r
    gy0, gy1 = y - 0.02 * r, y + 0.62 * r
    gw, gh = gx1 - gx0, gy1 - gy0
    best = None
    for cols in range(1, k + 1):
        rows = math.ceil(k / cols)
        fw = min(gw / (cols + 0.12 * (cols - 1)), (gh / (rows + 0.12 * (rows - 1))) * W / H)
        if best is None or fw > best[0]:
            best = (fw, cols, rows)
    fw, cols, rows = best
    fh = fw * H / W
    g = 0.12 * fw
    tw, th = cols * fw + (cols - 1) * g, rows * fh + (rows - 1) * g
    ox, oy = x - tw / 2, gy0 + (gh - th) / 2
    out = []
    for i in range(k):
        rr, cc = divmod(i, cols)
        n_in_row = min(cols, k - rr * cols)
        row_w = n_in_row * fw + (n_in_row - 1) * g
        rx = x - row_w / 2
        out.append((rx + cc * (fw + g), oy + rr * (fh + g), fw / W))
    return out


# ------------------------------------------------------------------ assembly
def build(asm, phys: list[tuple[dict, list[El]]], sections: list[str]) -> list[Slide]:
    """Turn the physical slides into camera views of one canvas world."""
    from . import templates
    b, vl, m = asm.b, asm.vl, asm.m
    spec = dict(getattr(m, "world", None) or {})
    topics: list[dict] = []
    slide_topic: list[int | None] = []
    for ls, els in phys:
        sec = ls.get("section", "")
        if ls["type"] == "title" or not sec:
            slide_topic.append(None)
            continue
        if not topics or topics[-1]["section"] != sec:
            topics.append({"section": sec, "frames": [], "section_slide": None})
        ti = len(topics) - 1
        if ls["type"] == "section" and topics[ti]["section_slide"] is None and not topics[ti]["frames"]:
            topics[ti]["section_slide"] = len(slide_topic)
        else:
            topics[ti]["frames"].append(len(slide_topic))
        slide_topic.append(ti)
    if not topics:
        return []
    info = plan_world(spec.get("template", "road"), len(topics), int(spec.get("seed", 7)), spec.get("custom"))
    labels = [t["section"] for t in topics]
    cache = Path(__file__).resolve().parents[2] / "outputs" / "_cache" / "canvas"
    art = draw_art(b, info, labels, str(spec.get("core", ""))[:14], cache)
    r = info["r"]
    for ti, t in enumerate(topics):
        x, y = info["anchors"][ti]
        t["xy"] = (x, y)
        t["cam"] = topic_cam(x, y, r)
        t["grid"] = frame_grid(x, y, r, len(t["frames"]))

    # where every physical slide's own elements live (src) and where its camera looks (cam)
    n = len(phys)
    src: list[tuple] = [overview_cam()] * n
    cam: list[tuple] = [overview_cam()] * n
    for ti, t in enumerate(topics):
        if t["section_slide"] is not None:
            src[t["section_slide"]] = cam[t["section_slide"]] = t["cam"]
        for j, f in zip(t["frames"], t["grid"]):
            src[j] = cam[j] = f
    for i in range(n):                                 # slides outside any topic (title) see the whole world
        if slide_topic[i] is None and phys[i][0]["type"] != "title":
            cam[i] = src[i] = overview_cam()

    # persistent layers: title text (overview), topic titles (inside each circle), frame cards
    ctx = templates.Ctx(b, vl)
    persistent: list[tuple[El, tuple]] = []           # (element in src slide inches, src)
    tz = info["title_zone"]
    ov = overview_cam()
    first = phys[0][0] if phys and phys[0][0]["type"] == "title" else None
    if first:
        c = first["content"]
        zx, zy, zw, zh = (v / ov[2] for v in tz)
        tsize = templates.shrink_to_fit(ctx, c.get("title", ""), zw, zh * 0.62, b.sizes["display"] * 0.8,
                                        spacing=0.95, floor=0.4)
        persistent += [
            (El("text", zx, zy, zw, zh * 0.62, name="!!cv_title", text=c.get("title", ""),
                style={"font": "heading", "size": tsize, "anchor": "b", "spacing": 0.95}), ov),
            (El("text", zx, zy + zh * 0.65, zw, zh * 0.24, name="!!cv_sub", text=c.get("subtitle", ""),
                style={"font": "body", "color": "muted",
                       "size": templates.shrink_to_fit(ctx, c.get("subtitle", ""), zw, zh * 0.24 * 0.95,
                                                       max(9, b.sizes["small"] * 0.75), font="body", floor=0.5)}), ov),
            (El("text", zx, zy + zh * 0.88, zw, zh * 0.12, name="!!cv_auth", text=c.get("authors", ""),
                style={"font": "body", "color": "muted",
                       "size": templates.shrink_to_fit(ctx, c.get("authors", ""), zw, zh * 0.12 * 0.95,
                                                       max(8, b.sizes["caption"] * 0.75), font="body", floor=0.5)}), ov)]
    cr = CIRCLE_FILL * H / 2                           # circle radius in topic-camera slide inches
    for ti, t in enumerate(topics):
        num = f"{ti + 1:02d}"
        if t["section_slide"] is not None:
            num = str(phys[t["section_slide"]][0]["content"].get("number", num))
        ttl = t["section"]
        if t["section_slide"] is not None:
            ttl = phys[t["section_slide"]][0]["content"].get("title", ttl)
        tw = 1.45 * cr
        tsize = templates.shrink_to_fit(ctx, ttl, tw, 1.25, b.sizes["title"] + 6, spacing=0.95, floor=0.5)
        persistent += [
            (El("text", W / 2 - tw / 2, H / 2 - 0.80 * cr, tw, 0.45, name=f"!!cv_num{ti}", text=num,
                style={"font": "mono", "size": b.sizes["small"], "color": "ours", "align": "c", "tracking": 1.5}),
             t["cam"]),
            (El("text", W / 2 - tw / 2, H / 2 - 0.62 * cr, tw, 1.25, name=f"!!cv_sec{ti}", text=ttl,
                style={"font": "heading", "size": tsize, "align": "c", "anchor": "m", "spacing": 0.95}),
             t["cam"])]

    def own(i):
        ls, els = phys[i]
        if ls["type"] in ("title", "section") and (i == 0 or slide_topic[i] is not None) and \
                (ls["type"] == "title" or topics[slide_topic[i]]["section_slide"] == i):
            return []                                  # replaced by the persistent title / topic texts
        return els

    slides: list[Slide] = []
    for i, (ls, _els) in enumerate(phys):
        cm = cam[i]
        s = Slide(ls["type"], elements=[], notes=ls.get("notes", ""), bg=world_bg(b),
                  meta={"id": ls.get("id"), "variant": ls.get("variant_used"), "section": ls.get("section", ""),
                        "canvas": True})
        s.elements.append(world_el("image", 0, 0, WW, WH, cm, name="!!cv_art", data=art,
                                   meaning="canvas world (the talk travels through it)"))
        for ti, t in enumerate(topics):                # the topic circles
            x, y = t["xy"]
            s.elements.append(world_el("ellipse", x - r, y - r, 2 * r, 2 * r, cm, name=f"!!cv_c{ti}",
                                       style={"fill": b.bg, "line": b.ours, "line_w": 0.022 * r * 72}))
        for ti, t in enumerate(topics):                # frame cards (also the slide's own background)
            for j, (fx, fy, fs) in zip(t["frames"], t["grid"]):
                s.elements.append(world_el("rect", fx, fy, W * fs, H * fs, cm, name=f"!!cv_f{j}",
                                           style={"fill": b.bg, "line": mix(b.bg, b.text, 0.14),
                                                  "line_w": 0.004 * W * fs * 72, "corner": "sharp"}))
        for el, sc in persistent:
            p = place(el, sc, cm, b, vl)
            if p is not None and el.name.startswith(("!!cv_title", "!!cv_sub", "!!cv_auth")) and cm != ov:
                on_screen = p.x < W and p.x + p.w > 0 and p.y < H and p.y + p.h > 0
                if on_screen:                          # the overview title, blown up behind a closer view: leave it out
                    p = None
            if p is not None:
                p.role = "world" if p.role == "content" else p.role
                s.elements.append(p)
        ti_i = slide_topic[i]
        for j in range(n):                            # content of the frames nearby
            if j != i and not (ti_i is not None and slide_topic[j] == ti_i) and abs(j - i) > 1:
                continue
            for k_, el in enumerate(own(j)):
                if el.role in ("motif", "chrome"):
                    continue
                if el.role == "world" and el.name == "!!backdrop":
                    continue
                p = place(el, src[j], cm, b, vl)
                if p is None:
                    continue
                p.name = f"!!cv_{j}_{k_}"
                if j != i:
                    p.build = None
                    p.style["_ghost"] = True
                s.elements.append(p)
        s.transition = "fade" if i == 0 else "morph"
        slides.append(s)
    return slides


# ------------------------------------------------------------------ custom worlds (drawn by the director as SVG)
def svg_shapes(svg: str):
    """Parse the director's SVG into reified shapes (transforms applied). Raises ValueError if unusable."""
    import io
    import svgelements as se
    try:
        doc = se.SVG.parse(io.StringIO(svg), reify=True, width=SVG_W, height=SVG_H)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"the SVG does not parse: {e}") from e
    out = [el for el in doc.elements() if isinstance(el, (se.Shape, se.Text))]
    if not out:
        raise ValueError("the SVG has no drawable shapes")
    return out


def svg_colours(svg: str) -> set[str]:
    """Every colour the drawing uses (#RRGGBB, upper case)."""
    import svgelements as se
    cols = set()
    for el in svg_shapes(svg):
        for c in (getattr(el, "fill", None), getattr(el, "stroke", None)):
            if c is not None and c.value is not None and c.alpha:
                cols.add(se.Color(c).hex[:7].upper())
    return cols


def draw_svg(ax, svg: str, b: Brand, k: float):
    """Render the director's SVG onto the art axes (world inches = svg units * k)."""
    import svgelements as se
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath
    fh, fb = _font(b, True), _font(b, False)
    for z, el in enumerate(svg_shapes(svg)):
        if isinstance(el, se.Text):
            if not el.text:
                continue
            col = el.fill if el.fill is not None and el.fill.value is not None else se.Color("#" + b.text)
            size = (el.font_size or 24) * k * 72
            bold = str(getattr(el, "font_weight", "")).lower() in ("bold", "600", "700", "800", "900")
            anchor = {"start": "left", "middle": "center", "end": "right"}.get(getattr(el, "anchor", "start"), "left")
            ax.text(el.x * k, el.y * k, el.text, fontproperties=fh if bold else fb, fontsize=size,
                    color=se.Color(col).hex[:7], alpha=(col.alpha or 255) / 255, ha=anchor, va="baseline", zorder=6)
            continue
        path = se.Path(el)
        verts, codes = [], []
        for seg in path.segments():
            if isinstance(seg, se.Move):
                verts.append((seg.end.x * k, seg.end.y * k)); codes.append(MPath.MOVETO)
            elif isinstance(seg, se.Close):
                verts.append(verts[-1] if verts else (0, 0)); codes.append(MPath.CLOSEPOLY)
            elif isinstance(seg, se.Line):
                verts.append((seg.end.x * k, seg.end.y * k)); codes.append(MPath.LINETO)
            elif isinstance(seg, se.QuadraticBezier):
                verts += [(seg.control.x * k, seg.control.y * k), (seg.end.x * k, seg.end.y * k)]
                codes += [MPath.CURVE3, MPath.CURVE3]
            elif isinstance(seg, se.CubicBezier):
                verts += [(seg.control1.x * k, seg.control1.y * k), (seg.control2.x * k, seg.control2.y * k),
                          (seg.end.x * k, seg.end.y * k)]
                codes += [MPath.CURVE4] * 3
            elif isinstance(seg, se.Arc):
                for cb in seg.as_cubic_curves():
                    verts += [(cb.control1.x * k, cb.control1.y * k), (cb.control2.x * k, cb.control2.y * k),
                              (cb.end.x * k, cb.end.y * k)]
                    codes += [MPath.CURVE4] * 3
        if len(verts) < 2 or codes[0] != MPath.MOVETO:
            continue
        fill, stroke = el.fill, el.stroke
        has_fill = fill is not None and fill.value is not None and fill.alpha
        has_stroke = stroke is not None and stroke.value is not None and stroke.alpha and (el.stroke_width or 0) > 0
        if not (has_fill or has_stroke):
            continue
        dash = None
        try:
            raw = str(el.values.get("stroke-dasharray", "") or "")
            nums = [float(v) for v in raw.replace(",", " ").split()] if raw not in ("", "none") else []
            if nums and has_stroke:
                lw_pt = max((el.stroke_width or 1) * k * 72, 0.1)
                dash = (0, [v * k * 72 / lw_pt for v in nums])
        except Exception:  # noqa: BLE001
            dash = None
        ax.add_patch(PathPatch(MPath(verts, codes),
                               fc=se.Color(fill).hex[:7] if has_fill else "none",
                               ec=se.Color(stroke).hex[:7] if has_stroke else "none",
                               lw=(el.stroke_width or 0) * k * 72 if has_stroke else 0,
                               alpha=None, zorder=1 + z * 1e-4, capstyle="round", joinstyle="round",
                               fill=bool(has_fill), linestyle=dash or "solid"))
        if has_fill and fill.alpha < 255:
            ax.patches[-1].set_alpha(fill.alpha / 255)
        elif has_stroke and not has_fill and stroke.alpha < 255:
            ax.patches[-1].set_alpha(stroke.alpha / 255)


def check_custom(custom: dict, n: int, b: Brand) -> list[str]:
    """What the director must fix in a custom world (fed back to it)."""
    errs = []
    svg = custom.get("svg") or ""
    if len(svg) > 60000:
        errs.append("the SVG is too long (max 60k characters): simplify the drawing")
    try:
        shapes = svg_shapes(svg)
        if len(shapes) > 600:
            errs.append(f"{len(shapes)} shapes – keep the world under 600 shapes")
        allowed = {("#" + c).upper() for c in (b.bg, b.surface, b.text, b.muted, b.ours, b.baseline, b.highlight,
                                               b.accent2, world_bg(b))}
        bad = sorted(svg_colours(svg) - allowed)
        if bad:
            errs.append(f"use only the palette colours (with opacity if needed); not allowed: {', '.join(bad[:6])}")
    except ValueError as e:
        errs.append(str(e))
    a = custom.get("anchors") or []
    if len(a) != n:
        errs.append(f"give exactly {n} anchors (one per section, in talk order); got {len(a)}")
    r = _radius(n) * SVG_W / WW
    for i, p in enumerate(a):
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            errs.append(f"anchor {i + 1} must be [x, y]")
            continue
        x, y = p
        if not (r <= x <= SVG_W - r and r <= y <= SVG_H - r):
            errs.append(f"anchor {i + 1} {p}: the circle (radius {r:.0f}) must lie inside 0..{SVG_W} x 0..{SVG_H}")
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            try:
                if math.hypot(a[i][0] - a[j][0], a[i][1] - a[j][1]) < 2 * r + 20:
                    errs.append(f"anchors {i + 1} and {j + 1} are closer than {2 * r + 20:.0f}: the circles would overlap")
            except Exception:  # noqa: BLE001
                pass
    tz = custom.get("title_zone")
    if not (isinstance(tz, list) and len(tz) == 4 and tz[2] >= 380 and tz[3] >= 180):
        errs.append("title_zone must be [x, y, w, h] with w >= 380 and h >= 180 (an empty area for the talk title)")
    else:
        for i, pt in enumerate(a):
            try:
                nx = min(max(pt[0], tz[0]), tz[0] + tz[2])
                ny = min(max(pt[1], tz[1]), tz[1] + tz[3])
                if math.hypot(pt[0] - nx, pt[1] - ny) < r + 10:
                    errs.append(f"anchor {i + 1}: its circle overlaps the title_zone")
            except Exception:  # noqa: BLE001
                pass
    return errs
