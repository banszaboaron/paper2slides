"""Assembly: logical deck spec -> physical slides.

Steps (brief, stage 5): expand steps (zoom) -> choose template variant (measured fit)
-> chrome + motion -> moderation budget + subtractive critic -> lint.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PIL import Image

from . import check, grounds, rules, templates
from .brand import Brand, VisualLanguage, Motion
from .motion import apply_motion, chrome, snap_region, zoom_crop
from .spec import El, Slide, W, H


class Assembler:
    def __init__(self, b: Brand, vl: VisualLanguage, m: Motion, motif=None, world_img: str | None = None):
        self.b, self.vl, self.m = b, vl, m
        self.ctx = templates.Ctx(b, vl, motif)
        self.world_img = world_img
        self.log: list[str] = []

    # -------------------------------------------------------- variant choice
    def choose(self, ls: dict, prev_variant: str | None) -> tuple[str, list[El]]:
        kind = ls["type"]
        variants = templates.LIBRARY[kind]
        if ls.get("variant"):
            order = [ls["variant"]]
        else:
            pref = (self.b.layouts or {}).get(kind)
            order = ([pref] if pref in variants else []) + [v for v in variants if v != pref]
        if prev_variant in order and len(order) > 1:          # avoid repeating the same layout back-to-back
            order.remove(prev_variant)
            order.append(prev_variant)
        best, wrong = None, []
        for v in order:
            try:
                els = variants[v](self.ctx, ls["content"])
            except (KeyError, TypeError, IndexError) as e:   # the content is for another variant of the type
                wrong.append(f"{v} ({type(e).__name__}: {e})")   # (e.g. the style prefers a big number,
                continue                                     #  the slide has a sentence)
            probe = Slide(kind, elements=els)
            errs = [i for i in check.check_deck([probe], self.b) if i.level == "error" and i.code in ("overflow", "bounds")]
            score = len(errs) * 10 + self._aspect_penalty(v, ls["content"])
            if best is None or score < best[0]:
                best = (score, v, els)
            if score == 0:
                break
        if best is None:
            raise KeyError(f"{ls.get('id', kind)}: the content fits no {kind} layout: " + "; ".join(wrong))
        self.log.append(f"{ls.get('id', kind)}: {best[1]} (score {best[0]:.2f})")
        return best[1], best[2]

    @staticmethod
    def _aspect_penalty(variant: str, c: dict) -> float:
        if "image" not in c:
            return 0
        im = Image.open(c["image"])
        ar = im.width / im.height
        want = {"figure_left_text_right": 1.4, "figure_right_text_left": 1.4, "figure_top_text_bottom": 3.5,
                "figure_full": 2.4}.get(variant)
        return abs(ar - want) / want * 0.5 if want else 0

    # -------------------------------------------------------- zoom steps
    def expand_zoom(self, ls: dict, base_els: list[El]) -> list[tuple[dict, list[El]]]:
        out = [(ls, base_els)]
        z = ls.get("zoom")
        if not z or self.m.mode in ("none", "basic"):
            return out
        img = next(e for e in base_els if e.kind == "image")
        img.name = img.name or "!!zoomfig"
        ar = Image.open(img.data).width / Image.open(img.data).height
        for k, step in enumerate(z):
            fw = templates.Ctx(self.b, self.vl).cw * 0.62
            area_h = templates.BODY_BOTTOM - templates.BODY_Y
            rx0, ry0, rx1, ry1 = snap_region(img.data, step["region"])   # never cut through a label
            # scale relative to the base slide, limited by the area and by zoom_max (brief: <= 3x)
            reg_w_in, reg_h_in = img.w * (rx1 - rx0), img.h * (ry1 - ry0)
            sc = min(self.m.zoom_max, fw / reg_w_in, area_h / reg_h_in)
            nw, nh = reg_w_in * sc, reg_h_in * sc
            nx, ny = self.b.margin, templates.BODY_Y + (area_h - nh) / 2
            crop = (rx0, ry0, 1 - rx1, 1 - ry1)
            zimg = El("image", nx, ny, nw, nh, name=img.name, data=img.data, meaning=img.meaning,
                      style={"crop": crop})
            tx = self.b.margin + nw + 3 * self.b.gutter
            tw = min(W - self.b.margin - tx, 6.2)
            els = [copy.deepcopy(e) for e in base_els if e.name == "!!title"]
            els.append(zimg)
            els.append(El("text", tx, templates.BODY_Y + 0.2, tw, 0.6, text=step.get("label", ""),
                          style={"font": "mono", "size": self.b.sizes["small"], "color": "ours"}))
            els.append(templates.body(self.ctx, tx, templates.BODY_Y + 0.9, tw, 3.6, step["text"],
                                      size=self.b.sizes["body"]))
            sub = dict(ls, id=f"{ls.get('id')}.zoom{k + 1}", type="figure_text",   # the script stays on the first one
                       notes=f"(zoom {k + 1}: {step.get('label', '')} – same part of the script as the previous slide)")
            out.append((sub, els))
        if z and z[-1].get("return", True):
            back = [copy.deepcopy(e) for e in base_els]
            out.append((dict(ls, id=f"{ls.get('id')}.back", notes="(back to the whole figure)"), back))
        return out

    # -------------------------------------------------------- whole deck
    def build(self, deck: dict) -> list[Slide]:
        logical = deck["slides"]
        sections = [s for s in dict.fromkeys(ls.get("section", "") for ls in logical) if s]
        phys: list[tuple[dict, list[El]]] = []
        prev = None
        for ls in logical:
            v, els = self.choose(ls, prev)
            prev = v
            ls = dict(ls, variant_used=v)
            phys.extend(self.expand_zoom(ls, els))
        if self.m.mode == "world" and sections:       # Prezi-style: one canvas, every slide is a camera on it
            from . import canvas
            slides = canvas.build(self, phys, sections)
            if slides:
                self.log.append(f"canvas world: {(getattr(self.m, 'world', None) or {}).get('template', 'road')}, "
                                f"{len(slides)} camera views")
                return slides
        n = len(phys)
        slides = []
        for i, (ls, els) in enumerate(phys, 1):
            s = Slide(ls["type"], elements=[], notes=ls.get("notes", ""),
                      meta={"id": ls.get("id"), "variant": ls.get("variant_used"), "section": ls.get("section", "")})
            sec = ls.get("section", "")
            sec_no = sections.index(sec) + 1 if sec in sections else 0
            g = getattr(self.m, "ground", None) or {}
            field_col = None
            if ls["type"] == "section" and g.get("section") in ("surface", "ours", "text"):
                field_col = getattr(self.b, g["section"])
            if self.m.mode == "world":
                s.elements.extend(self.world_layer(ls, sec_no, sections, i))
            elif field_col is None:
                ge = grounds.ground_el(self.b, g.get("kind", "solid"), ls["type"])
                if ge is not None:
                    s.elements.append(ge)
            s.elements.extend(self.backdrop(ls["type"]))
            s.elements.extend(els)
            if self.b.title_rule and ls["type"] not in ("title", "section", "statement", "closing"):
                s.elements.append(El("rect", self.b.margin, templates.TITLE_Y + templates.TITLE_H - 0.02, 0.9, 0.07,
                                     name="!!titlerule", role="chrome", style={"fill": self.b.ours, "corner": "sharp"}))
            if ls["type"] not in ("title",):
                s.elements.extend(chrome(self.b, i, n, sec_no, sec, show_section=ls["type"] not in ("section", "closing"),
                                         style=getattr(self.m, "chrome", "bar"), sections=sections))
            if ls.get("bg"):
                s.bg = ls["bg"]
            if field_col:                                 # section divider on a colour field
                s.bg = field_col
                if g["section"] in ("ours", "text"):
                    grounds.recolour_on(s.elements, field_col)
            slides.append(s)
        apply_motion(slides, self.m)
        self.moderate(slides)
        edits_by_id = {ls.get("id"): ls["_edit"] for ls in logical if ls.get("_edit")}
        if edits_by_id:                               # the Polish step's design edits win over the templates
            from . import edits
            for s in slides:
                e = edits_by_id.get(str(s.meta.get("id") or "").split(".")[0])
                if e:
                    s._edit_problems = edits.apply(s, e, self.b)
        if getattr(self, "with_video", False):
            self.add_pipeline_video(slides)
        return slides

    def add_pipeline_video(self, slides: list[Slide]):
        """assets/pipeline_video.mp4 (if present) as a full-screen slide right after the workflow slide;
        it plays by itself, the next click goes on to the thank-you slide. Counts as part of 'workflow'."""
        from .spec import W, H
        root = Path(__file__).resolve().parents[2] / "assets"
        vid, poster = root / "pipeline_video.mp4", root / "pipeline_video_poster.jpg"
        if not vid.exists():
            return
        k = next((i for i, s in enumerate(slides) if str(s.meta.get("id")) == "workflow"), None)
        if k is None:
            return
        v = Slide("video", elements=[El("video", 0, 0, W, H, name="pipeline_video", role="content",
                                        meaning="video of the whole pipeline", data=str(vid),
                                        style={"poster": str(poster) if poster.exists() else None})],
                  bg="000000", transition="fade",
                  notes="[video, plays by itself, ~65 s] The whole pipeline in one minute. Next click: thank you.",
                  meta={"id": "workflow.video", "variant": "video", "section": slides[k].meta.get("section", "")})
        slides.insert(k + 1, v)
        self.log.append("pipeline video slide after 'workflow'")

    # -------------------------------------------------------- world-Morph
    def world_layer(self, ls: dict, sec_no: int, sections: list[str], idx: int) -> list[El]:
        """Camera for this slide. The morph director's path says which concept each slide stands at
        and how close; without a plan every section is one station."""
        from . import world as wd
        if not sections:
            return []
        plan = getattr(self.m, "plan", None) or {}
        path = plan.get("path") or {}
        sid = str(ls.get("id", "")).split(".")[0]
        if not hasattr(self, "_world"):
            spec = dict(getattr(self.m, "world", None) or {})
            spec.setdefault("kind", "graph")
            cache = Path(__file__).resolve().parents[2] / "outputs" / "_cache" / "world"
            order = list(dict.fromkeys(v.get("node") for v in path.values() if v.get("node")))
            if spec["kind"] in wd.KINDS_3D:
                self._world = ("3d", wd.model_3d(self.b, spec, sections, cache), cache)
            else:
                self._world = ("2d", wd.scene_2d(self.b, spec, sections, cache, order or None), cache)
            self._last = (None, "far")
            self._drift = {}
        mode, info, cache = self._world
        kind = ls["type"]
        if mode == "2d":
            if sid in path:
                node, zoom = path[sid].get("node"), path[sid].get("zoom", "mid")
            elif not path:                               # no director: sections are the stations
                node = None if kind == "title" or sec_no == 0 else f"s{sec_no}"
                zoom = "near" if kind == "section" else "mid"
            else:
                node, zoom = self._last
            if kind == "title" and sid not in path:
                node, zoom = None, "far"
            key = (node, zoom)
            drift = self._drift.get(key, -1) + 1 if key == self._last else 0
            self._drift[key] = drift
            self._last = key
            at = (0.78, 0.5) if kind == "section" else (0.5, 0.5)
            # the world must never compete with text: only dividers show it clearly
            strength = {"section": 0.5, "title": 0.62}.get(kind, 0.9)
            els = [wd.camera_2d(info, node, zoom, drift if kind != "section" else 0, at), wd.veil(self.b, strength)]
            if kind in ("section", "title"):             # keep the title side calm, the station side bright
                els.append(wd.El("rect", 0, 0, wd.W * 0.56, wd.H, name="veil_side", role="world",
                                 style={"fill": self.b.bg, "alpha": 0.55, "corner": "sharp"}))
            return els
        station = None if kind == "title" or sec_no == 0 else sec_no - 1
        rot = info["rotations_y"][station] if station is not None else 25.0
        img = wd.view_3d(self.b, info, rot, cache / f"{Path(info['glb']).stem}_r{int(rot * 10)}.png")
        el = wd.element_3d(img, None if kind in ("section", "title") else station)
        el.meta3d = {"glb": info["glb"], "rot_y": rot}
        return [el, wd.veil(self.b, 0.0 if kind in ("section", "title") else 0.72)]

    # -------------------------------------------------------- quiet backdrop shapes (layout stage)
    def backdrop(self, kind: str) -> list[El]:
        mode = getattr(self.b, "backdrop", "plain")
        if mode == "plain" or kind in ("title", "section", "statement", "closing"):
            return []
        st = {"fill": self.b.surface, "corner": "sharp", "allow_offslide": True}
        if mode == "band_left":
            return [El("rect", 0, 0, 0.32, H, name="!!backdrop", role="world", style=st)]
        if mode == "band_top":
            return [El("rect", 0, 0, W, 1.55, name="!!backdrop", role="world", style=st)]
        if mode == "corner":
            return [El("ellipse", W - 3.2, H - 2.6, 5.2, 5.2, name="!!backdrop", role="world",
                       style=dict(st, alpha=0.7))]
        return []

    # -------------------------------------------------------- moderation + subtractive critic
    def moderate(self, slides: list[Slide]):
        if self.m.mode == "world":                      # the world is the motif
            for i, s in enumerate(slides):
                if any(e.role == "motif" for e in s.elements):
                    self._drop(s, "motif", f"s{i + 1}: motif removed (world-Morph)")
        quiet = rules.MOTIF_BUDGET["quiet_types"]
        motif_slides = [i for i, s in enumerate(slides) if any(e.role == "motif" for e in s.elements)]
        for i in motif_slides:
            if slides[i].kind in quiet:
                self._drop(slides[i], "motif", f"s{i + 1}: motif removed (quiet type)")
        motif_slides = [i for i, s in enumerate(slides) if any(e.role == "motif" for e in s.elements)]
        budget = int(rules.MOTIF_BUDGET["max_slides_ratio"] * len(slides))
        keep_first = {0, len(slides) - 1}
        extra = [i for i in motif_slides if i not in keep_first]
        while len(motif_slides) > budget and extra:
            j = extra.pop()                                   # drop from the back (sections later in the talk)
            self._drop(slides[j], "motif", f"s{j + 1}: motif removed (budget {budget}/{len(slides)})")
            motif_slides.remove(j)
        for i, s in enumerate(slides):
            deco = [e for e in s.elements if e.role == "decoration"]
            for e in deco[rules.DECORATION_PER_SLIDE:]:
                s.elements.remove(e)
                self.log.append(f"s{i + 1}: decoration removed (> {rules.DECORATION_PER_SLIDE})")
            steps = sorted({e.build for e in s.elements if e.build})
            if len(steps) > rules.MAX_BUILDS:
                remap = {st: min(k + 1, rules.MAX_BUILDS) for k, st in enumerate(steps)}
                for e in s.elements:
                    if e.build:
                        e.build = remap[e.build]
                self.log.append(f"s{i + 1}: builds merged to {rules.MAX_BUILDS}")

    def _drop(self, s: Slide, role: str, msg: str):
        s.elements = [e for e in s.elements if e.role != role]
        self.log.append(msg)


def _fit_ar(ar, x, y, w, h):
    if w / h > ar:
        nw, nh = h * ar, h
    else:
        nw, nh = w, w / ar
    return x, y + (h - nh) / 2, nw, nh


def load_deck(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
