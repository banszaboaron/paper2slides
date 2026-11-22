"""World-Morph: one big 'world' object the talk travels through, section by section.

2D worlds (graph | map | orbit | flow) are one large picture; every slide shows it through a
'camera' (position + zoom of the same !!world picture), so PowerPoint Morph flies between
stations. Content slides put a translucent veil over the world so text stays readable;
section slides lift the veil and show the station.

3D worlds (globe3d | network3d) are a glTF model that PowerPoint rotates between slides
(like PELDA.pptx). python-pptx cannot insert 3D models, so the deck carries a picture of the
model at each station's rotation (a faithful fallback everywhere) and, on the laptop,
`office.add_3d_models` swaps those pictures for the real model through COM.

world spec (from the motion design agent):
    {"kind": "graph|map|orbit|flow|globe3d|network3d", "seed": 7, "label": "optional caption"}
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .brand import Brand
from .spec import El, W, H

KINDS_2D = ("graph", "map", "orbit", "flow")
KINDS_3D = ("globe3d", "network3d")
KINDS = KINDS_2D + KINDS_3D
SCALE = 4.0                      # world size in slide widths
DPI = 80


def _hex(c: str) -> str:
    return "#" + c.lstrip("#")


def _key(b: Brand, world: dict, sections: list[str], extra: str = "") -> str:
    raw = json.dumps([b.to_dict(), world, sections, extra], sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()[:10]


# ------------------------------------------------------------------ 2D worlds
def _stations_2d(kind: str, n: int, ww: float, wh: float, rng) -> list[tuple[float, float]]:
    n = max(n, 1)
    if kind == "orbit":
        cx, cy = ww / 2, wh / 2
        out = []
        for k in range(n):
            r = (0.12 + 0.36 * (k + 1) / n) * min(ww, wh * 1.6)
            a = -0.6 + k * 2.1
            out.append((cx + r * math.cos(a), cy + 0.55 * r * math.sin(a)))
        return out
    if kind == "flow":                                # a winding road from left to right
        return [(ww * (0.1 + 0.8 * k / max(n - 1, 1)), wh * (0.5 + 0.28 * math.sin(k * 2.2))) for k in range(n)]
    pts = []                                          # graph / map: spread out, roughly left -> right
    for k in range(n):
        x = ww * (0.12 + 0.76 * k / max(n - 1, 1)) + rng.uniform(-0.04, 0.04) * ww
        y = wh * (0.25 + 0.5 * rng.uniform())
        pts.append((x, y))
    return pts


def concepts(world: dict, sections: list[str]) -> tuple[list[dict], list[list[str]]]:
    """Nodes/edges of the world. The morph director names the paper's concepts; without it the
    sections become the nodes (a simple chain)."""
    nodes = [n for n in (world.get("nodes") or []) if n.get("id")]
    if not nodes:
        nodes = [{"id": f"s{k + 1}", "label": name} for k, name in enumerate(sections)]
        edges = [[f"s{k}", f"s{k + 1}"] for k in range(1, len(sections))]
        return nodes, edges
    ids = {n["id"] for n in nodes}
    edges = [e for e in (world.get("edges") or []) if len(e) >= 2 and e[0] in ids and e[1] in ids]
    return nodes, edges


def _layout(kind: str, nodes: list[dict], edges, order: list[str], ww: float, wh: float, seed: int) -> dict:
    import math as m
    import networkx as nx
    ids = [n["id"] for n in nodes]
    seq = order + [i for i in ids if i not in order]
    if kind == "flow":                                # a winding road in visiting order
        k = len(seq)
        return {nid: (ww * (0.08 + 0.84 * i / max(k - 1, 1)), wh * (0.5 + 0.26 * m.sin(i * 1.9))) for i, nid in enumerate(seq)}
    if kind == "orbit":                               # visiting order -> outward rings
        out = {}
        for i, nid in enumerate(seq):
            r = (0.14 + 0.34 * (i + 1) / len(seq)) * min(ww, wh * 1.7)
            a = -0.5 + i * 2.25
            out[nid] = (ww / 2 + r * m.cos(a), wh / 2 + 0.55 * r * m.sin(a))
        return out
    g = nx.Graph()
    g.add_nodes_from(ids)
    g.add_edges_from((a, c) for a, c, *_ in edges)
    for a, c in zip(seq, seq[1:]):                    # the travel path keeps neighbours close
        if not g.has_edge(a, c):
            g.add_edge(a, c, weight=0.6)
    pos = nx.kamada_kawai_layout(g) if len(ids) > 2 else {nid: (i, 0) for i, nid in enumerate(ids)}
    xs, ys = zip(*pos.values())
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx, sy = (x1 - x0) or 1, (y1 - y0) or 1
    return {nid: (ww * (0.1 + 0.8 * (x - x0) / sx), wh * (0.12 + 0.76 * (y - y0) / sy)) for nid, (x, y) in pos.items()}


def _font_props(b: Brand, heading=True):
    from matplotlib.font_manager import FontProperties
    from . import fonts
    table = {**fonts.FAMILY_FILES, **fonts._registry_files()}
    fam = b.heading_font if heading else b.body_font
    fn = table.get((fam, False)) or table.get((fam, True))
    return FontProperties(fname=str(fonts.FONT_DIR / fn)) if fn else FontProperties(family="DejaVu Sans")


def scene_2d(b: Brand, world: dict, sections: list[str], out_dir: Path, order: list[str] | None = None) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Polygon, Ellipse

    kind = world.get("kind", "graph")
    nodes, edges = concepts(world, sections)
    order = order or [n["id"] for n in nodes]
    out_dir.mkdir(parents=True, exist_ok=True)
    key = _key(b, world, sections, json.dumps(order))
    png = out_dir / f"world_{kind}_{key}.png"
    meta = out_dir / f"world_{kind}_{key}.json"
    if png.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8")) | {"image": str(png)}
    ww, wh = W * SCALE, H * SCALE
    rng = np.random.default_rng(world.get("seed", 7))
    pos = _layout(kind, nodes, edges, order, ww, wh, world.get("seed", 7))
    fig = plt.figure(figsize=(ww, wh), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, ww)
    ax.set_ylim(wh, 0)
    ax.axis("off")
    fig.patch.set_facecolor(_hex(b.bg))
    ours, base, surf, hi, muted, text = (_hex(b.ours), _hex(b.baseline), _hex(b.surface), _hex(b.highlight),
                                         _hex(b.muted), _hex(b.text))
    fp_h, fp_b = _font_props(b, True), _font_props(b, False)
    on_path = set(order)

    # background texture per world kind (quiet, no information)
    if kind == "graph":
        pts = rng.uniform([0, 0], [ww, wh], size=(140, 2))
        for p_ in pts:
            d = np.hypot(*(pts - p_).T)
            for j in np.argsort(d)[1:3]:
                ax.plot([p_[0], pts[j][0]], [p_[1], pts[j][1]], color=surf, lw=1.4, zorder=0)
        ax.scatter(pts[:, 0], pts[:, 1], s=30, color=surf, zorder=0)
    elif kind == "map":
        for nid, (x, y) in pos.items():               # one island per concept
            ang = np.linspace(0, 2 * np.pi, 70, endpoint=False)
            r = 4.2 * (1 + 0.18 * np.sin(3 * ang + rng.uniform(0, 6)) + 0.08 * np.sin(7 * ang + rng.uniform(0, 6)))
            ax.add_patch(Polygon(np.c_[x + r * np.cos(ang), y + 0.75 * r * np.sin(ang)], closed=True, fc=surf,
                                 ec="none", zorder=1))
    elif kind == "orbit":
        for nid, (x, y) in pos.items():
            r = math.hypot(x - ww / 2, (y - wh / 2) / 0.55)
            ax.add_patch(Ellipse((ww / 2, wh / 2), 2 * r, 1.1 * r, fc="none", ec=surf, lw=3, zorder=0))
        ax.add_patch(Ellipse((ww / 2, wh / 2), 3.2, 3.2, fc=hi, ec="none", zorder=1))
    elif kind == "flow":
        seq = sorted(pos.items(), key=lambda kv: kv[1][0])
        xs = np.linspace(0, ww, 500)
        ys = np.interp(xs, [0] + [p_[0] for _, p_ in seq] + [ww], [seq[0][1][1]] + [p_[1] for _, p_ in seq] + [seq[-1][1][1]])
        ys = np.convolve(np.pad(ys, 20, mode="edge"), np.ones(41) / 41, mode="valid")
        ax.plot(xs, ys, color=surf, lw=70, solid_capstyle="round", zorder=0)
        for nid, (x, y) in pos.items():
            pos[nid] = (x, float(np.interp(x, xs, ys)))

    # relations, then the travel path on top
    for e in edges:
        a, c = pos[e[0]], pos[e[1]]
        ax.plot([a[0], c[0]], [a[1], c[1]], color=base, lw=3, alpha=0.7, zorder=2)
        if len(e) > 2 and e[2]:
            ax.text((a[0] + c[0]) / 2, (a[1] + c[1]) / 2 - 0.35, str(e[2]), fontproperties=fp_b, fontsize=26,
                    color=muted, ha="center", va="bottom", zorder=3)
    for a, c in zip(order, order[1:]):
        if a in pos and c in pos:
            ax.plot([pos[a][0], pos[c][0]], [pos[a][1], pos[c][1]], color=ours, lw=6, alpha=0.9,
                    ls=(0, (2, 2)) if kind in ("map", "flow") else "-", zorder=4)
    for n in nodes:
        x, y = pos[n["id"]]
        big = n["id"] in on_path
        r = 1.05 if big else 0.6
        ax.add_patch(Ellipse((x, y), 2 * r, 2 * r, fc=ours if big else _hex(b.bg), ec=ours if big else base,
                             lw=5, zorder=5))
        ax.text(x, y + r + 0.45, n.get("label", n["id"]), fontproperties=fp_h, fontsize=60 if big else 42,
                color=text if big else muted, ha="center", va="top", zorder=6)
        if n.get("note"):
            ax.text(x, y + r + 1.75, n["note"], fontproperties=fp_b, fontsize=30, color=muted, ha="center",
                    va="top", zorder=6)
    fig.savefig(png, facecolor=fig.get_facecolor())
    plt.close(fig)
    info = {"kind": kind, "size": [ww, wh], "positions": {k: [float(v[0]), float(v[1])] for k, v in pos.items()},
            "nodes": nodes}
    meta.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return info | {"image": str(png)}


ZOOM = {"near": 1.0, "mid": 0.62, "far": None}


def camera_2d(info: dict, node: str | None, zoom: str = "mid", drift: int = 0, at=(0.5, 0.5)) -> El:
    """The world picture placed so that `node` sits at `at` (slide fractions); None or 'far' = overview."""
    ww, wh = info["size"]
    s = ZOOM.get(zoom, 0.62)
    if node is None or s is None or node not in info["positions"]:
        s = min(W / ww, H / wh) * 1.12
        cx, cy = ww / 2, wh / 2
        at = (0.5, 0.5)
    else:
        cx, cy = info["positions"][node]
        cx += 0.8 * drift
    return El("image", W * at[0] - cx * s, H * at[1] - cy * s, ww * s, wh * s, name="!!world", role="world",
              data=info["image"], meaning="world-Morph backdrop (exception: unifies the deck)",
              style={"allow_offslide": True})


def veil(b: Brand, strength: float) -> El:
    return El("rect", 0, 0, W, H, name="!!veil", role="world",
              style={"fill": b.bg, "alpha": strength, "corner": "sharp", "allow_offslide": True})


# ------------------------------------------------------------------ 3D worlds
def _noise3(p, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    v = np.zeros(len(p))
    for octave in range(5):
        k = rng.normal(size=3) * (1.6 * 2 ** octave)
        v += np.sin(p @ k + rng.uniform(0, 6.3)) / (1.7 ** octave)
    return v


def model_3d(b: Brand, world: dict, sections: list[str], out_dir: Path) -> dict:
    """A glTF model (.glb) of the world + the y-rotation of each station."""
    import numpy as np
    import trimesh

    kind = world.get("kind", "globe3d")
    out_dir.mkdir(parents=True, exist_ok=True)
    key = _key(b, world, sections, "3d")
    glb = out_dir / f"world_{kind}_{key}.glb"
    meta = out_dir / f"world_{kind}_{key}.json"
    if glb.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8")) | {"glb": str(glb)}

    def rgba(h, a=255):
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [a]

    n = max(len(sections), 1)
    rot = [round(-360 * k / n, 1) for k in range(n)]
    parts = []
    if kind == "globe3d":
        sph = trimesh.creation.icosphere(subdivisions=5, radius=1.0)
        v = sph.vertices
        land = _noise3(v, world.get("seed", 7)) > 0.35
        fc = np.where(land[sph.faces].mean(axis=1)[:, None] > 0.5, rgba(b.ours), rgba(b.surface))
        sph.visual.face_colors = fc
        parts.append(sph)
        for k in range(n):                            # a pin per station on the equator, facing the camera
            a = math.radians(90 + 360 * k / n)
            pin = trimesh.creation.icosphere(subdivisions=2, radius=0.07)
            pin.apply_translation([1.02 * math.cos(a), 0.15, 1.02 * math.sin(a)])
            pin.visual.face_colors = rgba(b.highlight)
            parts.append(pin)
    else:                                             # network3d: nodes on a sphere shell + edges
        rng = np.random.default_rng(world.get("seed", 7))
        pts = rng.normal(size=(42, 3))
        pts /= np.linalg.norm(pts, axis=1)[:, None]
        pts *= rng.uniform(0.75, 1.0, size=(42, 1))
        for k in range(n):
            a = math.radians(90 + 360 * k / n)
            pts[k] = [0.95 * math.cos(a), 0.0, 0.95 * math.sin(a)]
        for i, p in enumerate(pts):
            node = trimesh.creation.icosphere(subdivisions=2, radius=0.09 if i < n else 0.045)
            node.apply_translation(p)
            node.visual.face_colors = rgba(b.ours if i < n else b.baseline)
            parts.append(node)
            d = np.linalg.norm(pts - p, axis=1)
            for j in np.argsort(d)[1:3]:
                if j > i:
                    seg = trimesh.creation.cylinder(radius=0.012, segment=[p, pts[j]], sections=8)
                    seg.visual.face_colors = rgba(b.baseline, 200)
                    parts.append(seg)
    mesh = trimesh.util.concatenate(parts)
    mesh.export(str(glb))
    info = {"kind": kind, "rotations_y": rot}
    meta.write_text(json.dumps(info), encoding="utf-8")
    return info | {"glb": str(glb), "mesh_parts": len(parts)}


def view_3d(b: Brand, info: dict, rot_y: float, out: Path, px: int = 1100) -> Path:
    """Fallback picture of the 3D model at one rotation (what the deck shows without COM)."""
    if out.exists():
        return out
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import trimesh
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh = trimesh.load(info["glb"], force="mesh")
    a = math.radians(rot_y)
    R = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    v = mesh.vertices @ R.T
    tri = v[mesh.faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None] + 1e-9
    front = normals[:, 2] > -0.05
    shade = 0.55 + 0.45 * np.clip(normals[:, 2] * 0.8 + normals[:, 1] * 0.3, 0, 1)
    cols = mesh.visual.face_colors[:, :3] / 255.0
    cols = np.clip(cols * shade[:, None], 0, 1)
    order = np.argsort(tri[:, :, 2].mean(axis=1))
    order = [i for i in order if front[i]]
    fig = plt.figure(figsize=(px / 100, px / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.axis("off")
    from matplotlib.collections import PolyCollection
    ax.add_collection(PolyCollection(tri[order][:, :, :2], facecolors=cols[order], edgecolors=cols[order], lw=0.2))
    fig.savefig(out, transparent=True)
    plt.close(fig)
    return out


def element_3d(img: Path, station: int | None) -> El:
    """Where the model sits: big and half off the right edge on content slides, centred on section slides."""
    if station is None:
        size = 6.2
        return El("image", (W - size) / 2 + 2.6, (H - size) / 2, size, size, name="!!world3d", role="world",
                  data=str(img), meaning="world-Morph 3D object (exception: unifies the deck)",
                  style={"allow_offslide": True})
    size = 9.0
    return El("image", W - size * 0.42, (H - size) / 2 + 0.6, size, size, name="!!world3d", role="world",
              data=str(img), meaning="world-Morph 3D object (exception: unifies the deck)", style={"allow_offslide": True})
