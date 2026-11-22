"""Spec -> .pptx renderer (python-pptx + native XML for Morph/builds)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from .brand import Brand, VisualLanguage, Motion, graphics_of
from .spec import El, Slide, W, H
from . import xmlfx
from . import fonts as fontlib
from .mathtext import MATH_FONT, parse as parse_math, typography

CACHE = Path(__file__).resolve().parents[2] / "outputs" / "_cache"
ALIGN = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}
ANCHOR = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}
TOKEN = re.compile(r"(\$[^$]+\$|\*\*|==)")


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#").upper())


class Renderer:
    def __init__(self, brand: Brand, vl: VisualLanguage, motion: Motion):
        self.b, self.vl, self.m = brand, vl, motion
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.layout = self.prs.slide_layouts[6]
        self.g = graphics_of(brand)
        self.models3d: list[dict] = []

    # ------------------------------------------------------------ fonts / colors
    def font_of(self, style: dict) -> tuple[str, bool]:
        f = style.get("font", "body")
        if f == "heading":
            return self.b.heading_font, style.get("bold", self.b.heading_bold)
        if f == "body":
            return self.b.body_font, style.get("bold", False)
        if f == "mono":
            return self.b.mono_font, style.get("bold", False)
        return f, style.get("bold", False)

    def color(self, c: str | None, default: str) -> str:
        if not c:
            return default
        return getattr(self.b, c, c) if not re.fullmatch(r"[0-9A-Fa-f]{6}", c) else c

    # ------------------------------------------------------------ text
    def fill_text(self, tf, el: El):
        st = el.style
        family, bold = self.font_of(st)
        size = st.get("size", self.b.sizes["body"])
        base_col = self.color(st.get("color"), self.b.text)
        acc_col = self.color(st.get("accent"), self.b.ours)
        tf.word_wrap = True
        tf.auto_size = None
        m = st.get("pad", 0.0)
        tf.margin_left = tf.margin_right = Inches(m)
        tf.margin_top = tf.margin_bottom = Inches(st.get("vpad", m))
        tf.vertical_anchor = ANCHOR[st.get("anchor", "t")]
        paras = el.text.split("\n")
        for i, ptxt in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = ALIGN[st.get("align", "l")]
            if st.get("spacing"):
                p.line_spacing = st["spacing"]
            if i and st.get("para_gap"):
                p.space_before = Pt(st["para_gap"])
            is_b, is_a = False, False
            for tok in TOKEN.split(ptxt):
                if tok == "**":
                    is_b = not is_b
                    continue
                if tok == "==":
                    is_a = not is_a
                    continue
                if not tok:
                    continue
                col = acc_col if is_a else base_col
                if tok.startswith("$") and tok.endswith("$") and len(tok) > 2:
                    for mr in parse_math(tok[1:-1]):
                        self._run(p, mr.text, MATH_FONT, size, bold or is_b, col, st, italic=mr.italic,
                                  baseline=mr.baseline)
                    continue
                for seg, covered in self._coverage(typography(tok), family, bold or is_b):
                    self._run(p, seg, family if covered else MATH_FONT, size, bold or is_b, col, st)

    @staticmethod
    def _coverage(text: str, family: str, bold: bool):
        """Split text into segments the font has glyphs for / lacks (those fall back to the math font)."""
        if not fontlib.known(family):
            return [(text, True)]
        cmap = fontlib.metrics(family, bold).cmap
        segs: list[tuple[str, bool]] = []
        for ch in text:
            ok = ord(ch) in cmap or ch in "\n\t "
            if segs and segs[-1][1] == ok:
                segs[-1] = (segs[-1][0] + ch, ok)
            else:
                segs.append((ch, ok))
        return segs

    def _run(self, p, text, family, size, bold, color, st, italic=False, baseline=0):
        r = p.add_run()
        r.text = text
        f = r.font
        f.name = family
        f.size = Pt(max(1.0, size))
        f.bold = bold
        if italic:
            f.italic = True
        f.color.rgb = rgb(color)
        rPr = r._r.get_or_add_rPr()
        if baseline:
            rPr.set("baseline", str(baseline))
        for tag in ("a:ea", "a:cs"):
            e = etree.SubElement(rPr, qn(tag))
            e.set("typeface", family)
        if st.get("tracking"):
            rPr.set("spc", str(int(st["tracking"] * 100)))

    # ------------------------------------------------------------ shapes
    def _shape_kind(self, el: El):
        if el.kind == "ellipse":
            return MSO_SHAPE.OVAL
        corner = el.style.get("corner", self.vl.corner)
        if corner == "sharp":
            return MSO_SHAPE.RECTANGLE
        return MSO_SHAPE.ROUNDED_RECTANGLE

    def add_shape(self, slide, el: El):
        shp = slide.shapes.add_shape(self._shape_kind(el), Inches(el.x), Inches(el.y), Inches(el.w), Inches(el.h))
        st = el.style
        style = shp._element.find(qn("p:style"))
        if style is not None:  # theme style adds an effectRef shadow that LibreOffice honours
            shp._element.remove(style)
        if self._shape_kind(el) == MSO_SHAPE.ROUNDED_RECTANGLE:
            corner = st.get("corner", self.vl.corner)
            radius_in = st.get("radius", self.vl.radius)  # absolute radius in inches
            short = max(min(el.w, el.h), 0.01)
            if corner == "pill":
                radius_in = short / 2 if short <= 1.8 else 0.45  # big pills look like blobs
            shp.adjustments[0] = min(0.5, radius_in / short)
        fill = st.get("fill")
        if fill in (None, "none"):
            shp.fill.background()
        else:
            shp.fill.solid()
            shp.fill.fore_color.rgb = rgb(self.color(fill, fill))
            if st.get("alpha") is not None:
                self._alpha(shp.fill._xPr.find(qn("a:solidFill"))[0], st["alpha"])
        line = st.get("line")
        if line in (None, "none"):
            shp.line.fill.background()
        else:
            shp.line.color.rgb = rgb(self.color(line, line))
            shp.line.width = Pt(st.get("line_w", self.vl.stroke_w))
            if st.get("dash"):
                self._dash(shp.line, st["dash"])
        if st.get("shadow", self.vl.depth == "shadow" and el.role == "content"):
            self._shadow(shp)
        else:
            self._no_shadow(shp)
        if el.text:
            self.fill_text(shp.text_frame, el)
        return shp

    def add_line(self, slide, el: El):
        st = el.style
        con = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(el.x), Inches(el.y),
                                         Inches(el.x + el.w), Inches(el.y + el.h))
        style = con._element.find(qn("p:style"))
        if style is not None:
            con._element.remove(style)
        con.line.color.rgb = rgb(self.color(st.get("color"), self.b.muted))
        con.line.width = Pt(st.get("w", self.vl.stroke_w))
        dash = st.get("dash", "dash" if self.vl.arrow == "dashed" and st.get("arrow") else None)
        if dash:
            self._dash(con.line, dash)
        head = st.get("head", self.vl.arrow_head if st.get("arrow") else None)
        if head and head != "none":
            ln = con.line._get_or_add_ln()
            t = etree.SubElement(ln, qn("a:tailEnd"))
            t.set("type", head)
            t.set("w", "med")
            t.set("len", "med")
        return con

    def add_image(self, slide, el: El):
        path = str(el.data)
        fr = self.g["figure"] if el.role == "content" else {"frame": "none", "radius": 0, "shadow": False}
        k = el.style.get("_k", 1.0)                   # canvas world: frames seen from afar are scaled
        if fr.get("frame") == "card":
            pad = 0.14 * k
            card = El("rect", el.x - pad, el.y - pad, el.w + 2 * pad, el.h + 2 * pad, role="decoration",
                      style={"fill": self.b.surface, "corner": "round" if fr.get("radius", 0) else "sharp",
                             "radius": fr.get("radius", 0) * k + pad, "shadow": bool(fr.get("shadow"))})
            shp = self.add_shape(slide, card)
            shp.name = (el.name[2:] if el.name.startswith("!!") else el.name or "fig") + ":card"
        pic = slide.shapes.add_picture(path, Inches(el.x), Inches(el.y), Inches(el.w), Inches(el.h))
        crop = el.style.get("crop")
        if crop:
            pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = crop
        if el.meaning:
            pic._element.nvPicPr.cNvPr.set("descr", el.meaning)
        r = (fr.get("radius", 0) or 0) * k
        if r and el.role == "content":
            geom = pic._element.spPr.find(qn("a:prstGeom"))
            if geom is not None:
                geom.set("prst", "roundRect")
                av = geom.find(qn("a:avLst"))
                if av is None:
                    av = etree.SubElement(geom, qn("a:avLst"))
                gd = etree.SubElement(av, qn("a:gd"))
                gd.set("name", "adj")
                gd.set("fmla", f"val {int(min(0.5, r / max(min(el.w, el.h), 0.01)) * 100000)}")
        if fr.get("frame") == "border" and el.role == "content":
            pic.line.color.rgb = rgb(self.b.baseline)
            pic.line.width = Pt(1)
        if fr.get("shadow") and fr.get("frame") != "card" and el.role == "content":
            self._shadow(pic)
        return pic

    def add_formula(self, slide, el: El):
        png, wi, hi = render_latex(el.data, el.style.get("size", 30), self.color(el.style.get("color"), self.b.text),
                                   el.style.get("fontset", "cm"))
        scale = min(1.0, el.w / wi, el.h / hi)
        w, h = wi * scale, hi * scale
        ax = el.style.get("align", "c")
        x = el.x + (0 if ax == "l" else (el.w - w) / 2 if ax == "c" else el.w - w)
        y = el.y + (el.h - h) / 2
        pic = slide.shapes.add_picture(str(png), Inches(x), Inches(y), Inches(w), Inches(h))
        pic._element.nvPicPr.cNvPr.set("descr", "LaTeX: " + el.data)
        return pic

    def add_chart(self, slide, el: El):
        d = el.data
        cd = CategoryChartData()
        cd.categories = d["categories"]
        for s in d["series"]:
            cd.add_series(s["name"], s["values"])
        ctype = {"bar": XL_CHART_TYPE.COLUMN_CLUSTERED, "hbar": XL_CHART_TYPE.BAR_CLUSTERED,
                 "line": XL_CHART_TYPE.LINE}[d["type"]]
        gf = slide.shapes.add_chart(ctype, Inches(el.x), Inches(el.y), Inches(el.w), Inches(el.h), cd)
        ch = gf.chart
        fam, _ = self.font_of({"font": "body"})
        ch.font.name = fam
        ch.font.size = Pt(el.style.get("size", self.b.sizes["small"]))
        ch.font.color.rgb = rgb(self.b.muted)
        ch.has_legend = False
        ch.has_title = False
        va, ca = ch.value_axis, ch.category_axis
        gc = self.g["chart"]
        va.has_major_gridlines = bool(gc.get("gridlines"))
        if va.has_major_gridlines:
            va.major_gridlines.format.line.color.rgb = rgb(self.b.surface)
            va.major_gridlines.format.line.width = Pt(0.75)
        va.has_minor_gridlines = False
        va.visible = d.get("show_value_axis", bool(gc.get("gridlines")))
        if va.visible:
            va.format.line.fill.background()
            va.tick_labels.font.color.rgb = rgb(self.b.muted)
        if "y_min" in d:
            va.minimum_scale = d["y_min"]
        if "y_max" in d:
            va.maximum_scale = d["y_max"]
        if gc.get("axis_line", True):
            ca.format.line.color.rgb = rgb(self.b.baseline)
        else:
            ca.format.line.fill.background()
        ca.tick_labels.font.size = Pt(el.style.get("size", self.b.sizes["small"]))
        ca.tick_labels.font.color.rgb = rgb(self.b.text)
        from pptx.enum.chart import XL_TICK_MARK
        ca.major_tick_mark = XL_TICK_MARK.NONE
        plot = ch.plots[0]
        if d["type"] in ("bar", "hbar"):
            plot.gap_width = d.get("gap", gc.get("gap", 60))
            lab = gc.get("labels", "outside")
            pos = XL_LABEL_POSITION.INSIDE_END if lab == "inside" else XL_LABEL_POSITION.OUTSIDE_END
            plot.vary_by_categories = False
            plot.has_data_labels = lab != "none"
            dl = plot.data_labels
            dl.number_format = d.get("fmt", "0.0")
            dl.number_format_is_linked = False
            dl.position = pos
            dl.font.size = Pt(el.style.get("label_size", self.b.sizes["small"]))
            dl.font.color.rgb = rgb(self.b.text)
            roles = d.get("point_roles")
            for s_i, s in enumerate(plot.series):
                if lab != "none":
                    s.data_labels.show_value = True
                    s.data_labels.number_format = d.get("fmt", "0.0")
                    s.data_labels.number_format_is_linked = False
                    s.data_labels.position = pos
                srole = d["series"][s_i].get("role", "baseline")
                s.format.fill.solid()
                s.format.fill.fore_color.rgb = rgb(self.role_color(srole))
                if roles:
                    for p_i, r in enumerate(roles):
                        pt = s.points[p_i]
                        pt.format.fill.solid()
                        pt.format.fill.fore_color.rgb = rgb(self.role_color(r))
                        if r == "ours" and lab != "none":
                            pt.data_label.font.bold = True
                            pt.data_label.font.color.rgb = rgb(self.b.bg if lab == "inside" else self.b.ours)
                        elif lab == "inside":
                            pt.data_label.font.color.rgb = rgb(self.b.bg if self.b.dark else self.b.text)
        else:
            for s_i, s in enumerate(plot.series):
                srole = d["series"][s_i].get("role", "baseline")
                s.smooth = False
                s.format.line.color.rgb = rgb(self.role_color(srole))
                s.format.line.width = Pt(gc.get("line_w", 3.5) if srole == "ours" else 2)
                from pptx.enum.chart import XL_MARKER_STYLE
                if gc.get("markers"):
                    s.marker.style = XL_MARKER_STYLE.CIRCLE
                    s.marker.size = 7
                    s.marker.format.fill.solid()
                    s.marker.format.fill.fore_color.rgb = rgb(self.role_color(srole))
                    s.marker.format.line.fill.background()
                else:
                    s.marker.style = XL_MARKER_STYLE.NONE
                last = s.points[len(d["categories"]) - 1]
                last.data_label.has_text_frame = True
                last.data_label.text_frame.text = d["series"][s_i]["name"]
                last.data_label.position = XL_LABEL_POSITION.RIGHT
                for r in last.data_label.text_frame.paragraphs[0].runs:
                    r.font.size = Pt(self.b.sizes["small"])
                    r.font.bold = srole == "ours"
                    r.font.color.rgb = rgb(self.role_color(srole))
        return gf

    def role_color(self, role: str) -> str:
        if role == "baseline" and self.g["chart"].get("others") == "muted":
            return self.b.muted
        return {"ours": self.b.ours, "baseline": self.b.baseline, "highlight": self.b.highlight,
                "muted": self.b.muted, "accent2": self.b.accent2}.get(role, role)

    # ------------------------------------------------------------ xml helpers
    @staticmethod
    def _alpha(clr, alpha: float):
        a = etree.SubElement(clr, qn("a:alpha"))
        a.set("val", str(int(alpha * 100000)))

    @staticmethod
    def _dash(line, dash):
        ln = line._get_or_add_ln()
        p = etree.SubElement(ln, qn("a:prstDash"))
        p.set("val", {"dash": "dash", "dot": "sysDot"}.get(dash, dash))

    @staticmethod
    def _no_shadow(shp):
        spPr = shp._element.spPr
        if spPr.find(qn("a:effectLst")) is None:
            etree.SubElement(spPr, qn("a:effectLst"))

    @staticmethod
    def _shadow(shp):
        spPr = shp._element.spPr
        eff = etree.SubElement(spPr, qn("a:effectLst"))
        sh = etree.SubElement(eff, qn("a:outerShdw"))
        sh.set("blurRad", "152400")
        sh.set("dist", "38100")
        sh.set("dir", "5400000")
        sh.set("algn", "t")
        sh.set("rotWithShape", "0")
        c = etree.SubElement(sh, qn("a:srgbClr"))
        c.set("val", "000000")
        etree.SubElement(c, qn("a:alpha")).set("val", "18000")

    # ------------------------------------------------------------ slides
    def add_slide(self, s: Slide):
        slide = self.prs.slides.add_slide(self.layout)
        bg = slide.background.fill
        bg.solid()
        bg.fore_color.rgb = rgb(self.color(s.bg, self.b.bg))
        builds: dict[int, list[tuple[int, bool]]] = {}
        video_spid = None
        for i, el in enumerate(s.elements):
            if el.kind == "text":
                shp = slide.shapes.add_textbox(Inches(el.x), Inches(el.y), Inches(el.w), Inches(el.h))
                self.fill_text(shp.text_frame, el)
            elif el.kind in ("rect", "ellipse"):
                shp = self.add_shape(slide, el)
            elif el.kind == "line":
                shp = self.add_line(slide, el)
            elif el.kind == "image":
                shp = self.add_image(slide, el)
            elif el.kind == "formula":
                shp = self.add_formula(slide, el)
            elif el.kind == "chart":
                shp = self.add_chart(slide, el)
            elif el.kind == "video":
                shp = slide.shapes.add_movie(str(el.data), Inches(el.x), Inches(el.y), Inches(el.w), Inches(el.h),
                                             poster_frame_image=el.style.get("poster"), mime_type="video/mp4")
                video_spid = shp.shape_id
            else:
                raise ValueError(el.kind)
            shp.name = el.name or f"{el.role}:{el.kind}:{i}"
            if getattr(el, "meta3d", None):
                self.models3d.append({"slide": len(self.prs.slides), **el.meta3d})
            if el.build:
                builds.setdefault(el.build, []).append((shp.shape_id, el.kind in ("text", "rect", "ellipse")))
        if s.notes:
            slide.notes_slide.notes_text_frame.text = s.notes
        dur = self.m.morph_ms if s.transition == "morph" else self.m.fade_ms
        xmlfx.set_transition(slide, s.transition, dur)
        if builds:
            xmlfx.set_builds(slide, [builds[k] for k in sorted(builds)], self.m.build_ms)
        if video_spid is not None:
            xmlfx.set_autoplay(slide, video_spid)
        return slide

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        import datetime
        cp = self.prs.core_properties                 # not python-pptx's template defaults (2013, its author)
        cp.author, cp.last_modified_by, cp.comments = "paper2slides", "paper2slides", "Made with paper2slides"
        cp.created = cp.modified = datetime.datetime.now().replace(microsecond=0)
        cp.revision = 1
        self.prs.save(str(path))
        side = Path(str(path) + ".world3d.json")
        if self.models3d:
            import json
            side.write_text(json.dumps(self.models3d), encoding="utf-8")
        elif side.exists():
            side.unlink()
        return path


def render_deck(slides: list[Slide], brand, vl, motion, path):
    r = Renderer(brand, vl, motion)
    for s in slides:
        r.add_slide(s)
    return r.save(path)


def render_latex(latex: str, size_pt: float, color: str, fontset: str = "cm"):
    """Render LaTeX (mathtext subset) to a transparent PNG; returns (path, w_in, h_in)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    CACHE.mkdir(parents=True, exist_ok=True)
    latex = clean_latex(latex)
    key = hashlib.md5(f"{latex}|{size_pt}|{color}|{fontset}".encode()).hexdigest()[:12]
    out = CACHE / f"tex_{key}.png"
    dpi = 400
    if not out.exists():
        plt.rcParams["mathtext.fontset"] = fontset
        fig = plt.figure(figsize=(0.01, 0.01))
        try:
            latex_error(latex)
            fig.text(0, 0, f"${latex}$", fontsize=size_pt, color="#" + color)
        except ValueError:                             # never crash a whole deck on one formula: show it as text
            fig.text(0, 0, latex, fontsize=size_pt, color="#" + color)
        fig.savefig(out, dpi=dpi, transparent=True, bbox_inches="tight", pad_inches=0.04)
        plt.close(fig)
    im = Image.open(out)
    return out, im.width / dpi, im.height / dpi


def clean_latex(latex: str) -> str:
    """Display-formula source as matplotlib mathtext wants it: no surrounding $ / $$ / \\[ \\]."""
    t = str(latex or "").strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("$", "$")):
        if t.startswith(a) and t.endswith(b) and len(t) > len(a) + len(b):
            t = t[len(a):-len(b)].strip()
    return t


def latex_error(latex: str) -> str | None:
    """None if matplotlib mathtext can draw it; otherwise raises ValueError with the reason."""
    from matplotlib.mathtext import MathTextParser
    MathTextParser("path").parse(f"${clean_latex(latex)}$")
    return None
