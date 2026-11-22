"""Template library: every slide type has several fixed variants with measured slots.

Assembly only *chooses* a variant and fills slots; it never invents structure.
Each variant is a function (ctx, content) -> list[El]. Slot capacities are measured
with the real font files (see `catalog()`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PIL import Image

from . import fonts
from .brand import Brand, VisualLanguage, graphics_of
from .spec import El, W, H


@dataclass
class Ctx:
    b: Brand
    vl: VisualLanguage
    motif: Callable | None = None     # draws the paper motif (decoration) into a box

    @property
    def m(self):
        return self.b.margin

    @property
    def cw(self):
        return W - 2 * self.b.margin


TITLE_Y, TITLE_H = 0.52, 0.95
BODY_Y, BODY_BOTTOM = 1.72, 6.85


# ----------------------------------------------------------------- building blocks
TITLE_FLOOR = 0.8                 # a slide title may shrink to 80 % of the type scale's title size to stay on one line


def title(ctx: Ctx, text: str, w: float | None = None, y=TITLE_Y, size=None, name="!!title") -> El:
    w = w or ctx.cw
    size = size or shrink_to_fit(ctx, text, w, TITLE_H, ctx.b.sizes["title"], floor=TITLE_FLOOR)
    return El("text", ctx.m, y, w, TITLE_H, name=name, text=text,
              style={"font": "heading", "size": size, "anchor": "b"})


def title_chars(b, text: str | None = None, w: float | None = None) -> int:
    """How many characters of a slide title fit on its one line (smallest allowed size), measured."""
    w = w or (W - 2 * b.margin)
    size = b.sizes["title"] * TITLE_FLOOR
    words = (text or "Retrieval adds the missing geographic context so that the model answers the "
             "question correctly from the documents it was given").split()
    n = 0
    for k in range(1, len(words) + 1):
        if not fonts.fits(" ".join(words[:k]), b.heading_font, size, w, TITLE_H, b.heading_bold, 1.0)[0]:
            break
        n = len(" ".join(words[:k]))
    return n


def body(ctx: Ctx, x, y, w, h, text, size=None, build=None, **st) -> El:
    s = {"font": "body", "size": size or ctx.b.sizes["body"], "spacing": 1.12, "para_gap": 10}
    s.update(st)
    return El("text", x, y, w, h, text=text, style=s, build=build)


def bullets(items: list[str]) -> str:
    return "\n".join(f"• {t}" for t in items)


def card(ctx: Ctx, x, y, w, h, text, role="baseline", size=None, name="", build=None, align="c") -> El:
    """Diagram/comparison box following the visual language."""
    b, vl = ctx.b, ctx.vl
    col = b.ours if role == "ours" else (b.highlight if role == "highlight" else b.baseline)
    if vl.fill_style == "solid" and role in ("ours", "highlight"):
        st = {"fill": col, "color": "FFFFFF" if not b.dark else "0B0D12"}
    elif vl.fill_style == "outline":
        st = {"fill": "none", "line": col, "line_w": vl.stroke_w * (1.6 if role == "ours" else 1)}
    else:  # tint (or solid baseline cards)
        st = {"fill": b.surface, "line": col if role == "ours" else "none", "line_w": 2}
    st.update({"font": "body", "size": size or b.sizes["small"], "anchor": "m", "align": align, "pad": 0.14})
    return El("rect", x, y, w, h, name=name, text=text, style=st, build=build)


def arrow(ctx: Ctx, x1, y1, x2, y2, build=None, color=None) -> El | None:
    if ctx.vl.arrow == "none":
        return None
    return El("line", x1, y1, x2 - x1, y2 - y1, role="content",
              style={"arrow": True, "color": color or ctx.b.muted, "w": 1.75}, build=build)


def highlight(ctx: Ctx, x, y, w, h, build=None) -> El:
    """Emphasis mark around/under a region, per visual language."""
    b, kind = ctx.b, ctx.vl.highlight
    if kind == "underline":
        return El("rect", x, y + h + 0.04, w, 0.07, role="decoration", style={"fill": b.highlight, "corner": "sharp"}, build=build)
    if kind == "marker":
        return El("rect", x - 0.06, y + h * 0.45, w + 0.12, h * 0.55, role="decoration",
                  style={"fill": b.highlight, "alpha": 0.28, "corner": "sharp"}, build=build)
    if kind == "glow":
        return El("rect", x - 0.08, y - 0.06, w + 0.16, h + 0.12, role="decoration",
                  style={"fill": b.highlight, "alpha": 0.18, "radius": 0.12}, build=build)
    return El("rect", x - 0.1, y - 0.06, w + 0.2, h + 0.12, role="decoration",
              style={"fill": "none", "line": b.highlight, "line_w": 2.25, "radius": 0.1}, build=build)


def fit_image(path: str, x, y, w, h, align="c") -> tuple[float, float, float, float]:
    im = Image.open(path)
    ar = im.width / im.height
    if w / h > ar:
        nw, nh = h * ar, h
    else:
        nw, nh = w, w / ar
    nx = x + (w - nw) / 2 if align == "c" else (x if align == "l" else x + w - nw)
    return nx, y + (h - nh) / 2, nw, nh


def image(path, x, y, w, h, meaning, name="", align="c", build=None) -> El:
    nx, ny, nw, nh = fit_image(path, x, y, w, h, align)
    return El("image", nx, ny, nw, nh, name=name, data=path, meaning=meaning, build=build)


def caption(ctx: Ctx, x, y, w, text, align="l") -> El:
    return El("text", x, y, w, 0.42, text=text,
              style={"font": "body", "size": ctx.b.sizes["caption"] + 1, "color": "muted", "align": align, "label": True})


# ----------------------------------------------------------------- variants
def shrink_to_fit(ctx: Ctx, text, w, h, size, font="heading", spacing=1.0, floor=0.7):
    """Largest size <= `size` (down to floor*size) at which `text` fits; measured with the real font."""
    fam = ctx.b.heading_font if font == "heading" else ctx.b.body_font
    bold = ctx.b.heading_bold if font == "heading" else False
    s = size
    while s > size * floor and not fonts.fits(text, fam, s, w, h, bold, spacing)[0]:
        s -= 2
    return s


def title_left(ctx: Ctx, c: dict):
    b = ctx.b
    tsize = shrink_to_fit(ctx, c["title"], 8.4, 2.3, b.sizes["display"], spacing=0.95)
    els = [El("text", ctx.m, 2.0, 8.4, 2.3, name="!!title", text=c["title"],
              style={"font": "heading", "size": tsize, "anchor": "b", "spacing": 0.95}),
           El("text", ctx.m, 4.5, 8.4, 1.2, text=c.get("subtitle", ""),
              style={"font": "body", "size": b.sizes["h2"], "color": "muted"}),
           El("text", ctx.m, 6.0, 8.4, 0.8, text=c.get("authors", ""),
              style={"font": "body", "size": b.sizes["small"], "color": "muted"})]
    if ctx.motif:
        els += ctx.motif(9.5, 1.4, 3.2, 4.6)
    return els


def title_center(ctx: Ctx, c: dict):
    b = ctx.b
    els = [El("text", 1.4, 1.9, W - 2.8, 2.2, name="!!title", text=c["title"],
              style={"font": "heading", "size": b.sizes["display"], "anchor": "b", "align": "c", "spacing": 0.95}),
           El("text", 1.8, 4.3, W - 3.6, 0.9, text=c.get("subtitle", ""),
              style={"font": "body", "size": b.sizes["h2"], "color": "muted", "align": "c"}),
           El("text", 1.8, 6.0, W - 3.6, 0.6, text=c.get("authors", ""),
              style={"font": "body", "size": b.sizes["small"], "color": "muted", "align": "c"})]
    if ctx.motif:
        els += ctx.motif(W / 2 - 0.9, 0.55, 1.8, 1.1)
    return els


def section_number(ctx: Ctx, c: dict):
    b = ctx.b
    els = [El("text", ctx.m, 1.6, 3.6, 2.6, text=c.get("number", "01"),
              style={"font": "heading", "size": b.sizes["big"], "color": "ours", "anchor": "b"}),
           El("text", ctx.m, 4.3, 8.6, 1.6, name="!!title", text=c["title"],
              style={"font": "heading", "size": shrink_to_fit(ctx, c["title"], 8.6, 1.6, b.sizes["display"] - 8),
                     "anchor": "t"})]
    if ctx.motif:
        els += ctx.motif(9.6, 1.6, 3.0, 4.0)
    return els


def section_center(ctx: Ctx, c: dict):
    b = ctx.b
    return [El("text", 1.5, 2.2, W - 3, 0.7, text=c.get("number", "01"),
               style={"font": "mono", "size": b.sizes["h2"], "color": "ours", "align": "c"}),
            El("text", 1.5, 2.9, W - 3, 1.6, name="!!title", text=c["title"],
               style={"font": "heading", "size": b.sizes["display"] - 4, "align": "c", "anchor": "t"})]


def content_one(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    items = c["body"] if isinstance(c["body"], list) else [c["body"]]
    if c.get("build"):
        y = BODY_Y
        hh = min(1.15, (BODY_BOTTOM - BODY_Y) / len(items))
        for k, it in enumerate(items):
            els.append(body(ctx, ctx.m, y + k * hh, 8.6, hh, bullets([it]), build=min(k + 1, 3)))
    else:
        els.append(body(ctx, ctx.m, BODY_Y, 8.6, BODY_BOTTOM - BODY_Y, bullets(items)))
    return els


def content_two(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    items = c["body"]
    half = (len(items) + 1) // 2
    cw = (ctx.cw - ctx.b.gutter * 2) / 2
    els.append(body(ctx, ctx.m, BODY_Y, cw, BODY_BOTTOM - BODY_Y, bullets(items[:half])))
    els.append(body(ctx, ctx.m + cw + 2 * ctx.b.gutter, BODY_Y, cw, BODY_BOTTOM - BODY_Y, bullets(items[half:])))
    return els


def figure_full(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    els.append(image(c["image"], ctx.m, BODY_Y, ctx.cw, BODY_BOTTOM - BODY_Y - 0.5, c["meaning"], name=c.get("image_name", "")))
    if c.get("caption"):
        els.append(caption(ctx, ctx.m, BODY_BOTTOM - 0.42, ctx.cw, c["caption"]))
    return els


def figure_left_text_right(ctx: Ctx, c: dict, flip=False):
    els = [title(ctx, c["title"])]
    fw = ctx.cw * 0.6
    tw = ctx.cw - fw - ctx.b.gutter * 2
    fx = ctx.m + (tw + 2 * ctx.b.gutter if flip else 0)
    tx = ctx.m if flip else ctx.m + fw + 2 * ctx.b.gutter
    els.append(image(c["image"], fx, BODY_Y, fw, BODY_BOTTOM - BODY_Y, c["meaning"], name=c.get("image_name", ""),
                     align="r" if flip else "l"))
    items = c["body"] if isinstance(c["body"], list) else [c["body"]]
    els.append(body(ctx, tx, BODY_Y + 0.2, tw, BODY_BOTTOM - BODY_Y - 0.2, bullets(items), size=ctx.b.sizes["body"] - 1))
    return els


def figure_right_text_left(ctx, c):
    return figure_left_text_right(ctx, c, flip=True)


def figure_top_text_bottom(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    els.append(image(c["image"], ctx.m, BODY_Y, ctx.cw, 3.3, c["meaning"], name=c.get("image_name", "")))
    items = c["body"] if isinstance(c["body"], list) else [c["body"]]
    els.append(body(ctx, ctx.m, BODY_Y + 3.5, ctx.cw, BODY_BOTTOM - BODY_Y - 3.5, "   ·   ".join(items),
                    size=ctx.b.sizes["body"] - 1, align="c"))
    return els


def comparison_two(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    cw = (ctx.cw - ctx.b.gutter * 3) / 2
    for k, col in enumerate(c["columns"][:2]):
        x = ctx.m + k * (cw + 3 * ctx.b.gutter)
        role = col.get("role", "baseline")
        els.append(El("text", x, BODY_Y, cw, 0.6, text=col["head"],
                      style={"font": "heading", "size": ctx.b.sizes["h2"], "color": "ours" if role == "ours" else "muted"}))
        cd = card(ctx, x, BODY_Y + 0.75, cw, BODY_BOTTOM - BODY_Y - 0.75, bullets(col["items"]), role=role,
                  size=ctx.b.sizes["body"], align="l", build=col.get("build"))
        cd.style.update(anchor="t", pad=0.3, para_gap=12, spacing=1.1)
        need = fonts.text_height_in(cd.text, ctx.b.body_font, cd.style["size"], cd.w - 2 * cd.style["pad"],
                                    spacing=1.1, para_gap_pt=12) + 2 * cd.style["pad"] + 0.3
        cd.h = min(cd.h, max(need, 2.2))
        els.append(cd)
    return els


def comparison_three(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    n = len(c["columns"][:3])
    cw = (ctx.cw - ctx.b.gutter * 2 * (n - 1)) / n
    for k, col in enumerate(c["columns"][:3]):
        x = ctx.m + k * (cw + 2 * ctx.b.gutter)
        role = col.get("role", "baseline")
        els.append(El("text", x, BODY_Y, cw, 0.6, text=col["head"],
                      style={"font": "heading", "size": ctx.b.sizes["h2"], "color": "ours" if role == "ours" else "muted"}))
        cd = card(ctx, x, BODY_Y + 0.75, cw, BODY_BOTTOM - BODY_Y - 0.75, bullets(col["items"]), role=role,
                  size=ctx.b.sizes["body"] - 1, align="l", build=col.get("build"))
        cd.style.update(anchor="t", pad=0.26, para_gap=12, spacing=1.1)
        need = fonts.text_height_in(cd.text, ctx.b.body_font, cd.style["size"], cd.w - 2 * cd.style["pad"],
                                    spacing=1.1, para_gap_pt=12) + 2 * cd.style["pad"] + 0.3
        cd.h = min(cd.h, max(need, 2.2))
        els.append(cd)
    return els


def statement_sentence(ctx: Ctx, c: dict):
    b = ctx.b
    els = [El("text", 1.3, 1.6, W - 2.6, 3.8, text=c["text"],
              style={"font": "heading", "size": b.sizes["display"] - 10, "anchor": "m", "spacing": 1.05})]
    if c.get("source"):
        els.append(El("text", 1.3, 5.7, W - 2.6, 0.5, text=c["source"],
                      style={"font": "body", "size": b.sizes["small"], "color": "muted", "label": True}))
    return els


def statement_number(ctx: Ctx, c: dict):
    b = ctx.b
    nsize = shrink_to_fit(ctx, c["number"], ctx.cw, 3.4, b.sizes["big"] + 30, floor=0.5)
    return [El("text", ctx.m, 1.2, ctx.cw, 3.4, name=c.get("number_name", ""), text=c["number"],
               style={"font": "heading", "size": nsize, "color": "ours", "anchor": "b"}),
            El("text", ctx.m, 4.75, 8.4, 1.8, text=c["text"],
               style={"font": "body", "size": b.sizes["h2"] + 2, "spacing": 1.1})]


def formula_center(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    els.append(El("formula", ctx.m, 2.2, ctx.cw, 1.7, name=c.get("formula_name", ""), data=c["latex"],
                  style={"size": c.get("size", 44), "color": "text"}))
    terms = c.get("terms", [])
    if terms:
        n = len(terms)
        tw = (ctx.cw - (n - 1) * ctx.b.gutter * 2) / n
        for k, (sym, expl) in enumerate(terms):
            x = ctx.m + k * (tw + 2 * ctx.b.gutter)
            els.append(El("formula", x, 4.45, tw, 0.6, data=sym, style={"size": 22, "color": "ours"}, build=min(k + 1, 3)))
            els.append(body(ctx, x, 5.1, tw, 1.5, expl, size=ctx.b.sizes["small"], align="c", build=min(k + 1, 3)))
    return els


def evidence_chart_side(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    cw = ctx.cw * 0.66
    els.append(El("chart", ctx.m - 0.1, BODY_Y, cw, BODY_BOTTOM - BODY_Y, name=c.get("chart_name", ""), data=c["chart"],
                  style={"size": ctx.b.sizes["small"]}))
    tx = ctx.m + cw + ctx.b.gutter * 2
    els.append(body(ctx, tx, BODY_Y + 0.4, ctx.cw - cw - 2 * ctx.b.gutter, 3.6, c["takeaway"], size=ctx.b.sizes["h2"]))
    if c.get("source"):
        cap_el = caption(ctx, tx, BODY_BOTTOM - 0.85, ctx.cw - cw - 2 * ctx.b.gutter, c["source"])
        cap_el.h = 0.85
        cap_el.style["anchor"] = "b"
        els.append(cap_el)
    return els


def evidence_chart_full(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    els.append(El("chart", ctx.m - 0.1, BODY_Y, ctx.cw, BODY_BOTTOM - BODY_Y - 0.45, name=c.get("chart_name", ""),
                  data=c["chart"], style={"size": ctx.b.sizes["small"]}))
    if c.get("source"):
        els.append(caption(ctx, ctx.m, BODY_BOTTOM - 0.4, ctx.cw, c["source"]))
    return els


def evidence_table(ctx: Ctx, c: dict):
    """Dense evidence: a text grid (no decoration). rows[0] is the header."""
    els = [title(ctx, c["title"])]
    rows = c["rows"]
    ncol = len(rows[0])
    first_w = 2.4
    cw = (ctx.cw - first_w) / (ncol - 1)
    rh = min(0.52, (BODY_BOTTOM - BODY_Y - 0.5) / len(rows))
    hl = set(c.get("ours_rows", []))
    tg = graphics_of(ctx.b)["table"]
    for r, row in enumerate(rows):
        y = BODY_Y + r * rh
        if r == 0 and tg.get("header") == "fill":
            els.append(El("rect", ctx.m - 0.08, y, ctx.cw + 0.16, rh, role="table",
                          style={"fill": ctx.b.surface, "corner": "sharp"}))
        elif r and tg.get("stripes") and r % 2 == 0 and r not in hl:
            els.append(El("rect", ctx.m - 0.08, y, ctx.cw + 0.16, rh, role="table",
                          style={"fill": ctx.b.surface, "alpha": 0.55, "corner": "sharp"}))
        if r in hl:
            if tg.get("highlight") == "bar":
                els.append(El("rect", ctx.m - 0.2, y + 0.06, 0.07, rh - 0.12, role="table",
                              style={"fill": ctx.b.ours, "corner": "sharp"}))
            elif tg.get("highlight") != "bold":
                els.append(El("rect", ctx.m - 0.08, y, ctx.cw + 0.16, rh, role="table",
                              style={"fill": ctx.b.ours, "alpha": 0.10, "corner": "sharp"}))
        for k, cell in enumerate(row):
            x = ctx.m if k == 0 else ctx.m + first_w + (k - 1) * cw
            w = first_w if k == 0 else cw
            st = {"font": "body", "size": ctx.b.sizes["small"], "anchor": "m", "align": "l" if k == 0 else "c",
                  "label": True, "color": "muted" if r == 0 else ("ours" if r in hl else "text"), "bold": r in hl}
            els.append(El("text", x, y, w, rh, text=str(cell), style=st))
        if r == 0 and tg.get("header", "line") == "line":
            els.append(El("line", ctx.m, y + rh, ctx.cw, 0, role="table", style={"color": ctx.b.baseline, "w": 1}))
    if c.get("source"):
        els.append(caption(ctx, ctx.m, BODY_BOTTOM - 0.4, ctx.cw, c["source"]))
    return els


def diagram_flow(ctx: Ctx, c: dict):
    """Horizontal pipeline of 3-5 boxes with computed arrow endpoints."""
    els = [title(ctx, c["title"])]
    steps = c["steps"]
    c = dict(c, builds={int(k): v for k, v in c.get("builds", {}).items()})
    n = len(steps)
    gap = 0.55
    bw = (ctx.cw - gap * (n - 1)) / n
    bh = 1.7
    y = 2.75
    for k, st in enumerate(steps):
        x = ctx.m + k * (bw + gap)
        text, role = (st, "baseline") if isinstance(st, str) else (st["text"], st.get("role", "baseline"))
        if role == "ours" and ctx.vl.highlight != "box":
            els.append(highlight(ctx, x, y, bw, bh, build=c.get("builds", {}).get(k)))
        els.append(card(ctx, x, y, bw, bh, text, role=role, size=ctx.b.sizes["body"] - 2,
                        name=(st.get("name", "") if isinstance(st, dict) else ""), build=c.get("builds", {}).get(k)))
        if k:
            a = arrow(ctx, x - gap + 0.08, y + bh / 2, x - 0.08, y + bh / 2, build=c.get("builds", {}).get(k))
            if a:
                els.append(a)
    if c.get("caption"):
        els.append(body(ctx, ctx.m, y + bh + 0.6, ctx.cw, 1.3, c["caption"], size=ctx.b.sizes["body"], align="c",
                        color="muted"))
    return els


def closing_takeaways(ctx: Ctx, c: dict):
    b = ctx.b
    els = [El("text", ctx.m, 0.9, ctx.cw, 1.9, name="!!title", text=c["message"],
              style={"font": "heading", "size": b.sizes["display"] - 12, "anchor": "b", "spacing": 1.0})]
    items = c.get("takeaways", [])
    n = max(1, len(items))
    cw = (ctx.cw - (n - 1) * b.gutter * 2) / n
    for k, t in enumerate(items):
        x = ctx.m + k * (cw + 2 * b.gutter)
        els.append(El("text", x, 3.4, cw, 0.6, text=f"0{k + 1}", style={"font": "mono", "size": b.sizes["h2"], "color": "ours", "label": True}))
        els.append(body(ctx, x, 4.05, cw, 2.2, t, size=b.sizes["body"]))
    return els


def closing_thanks(ctx: Ctx, c: dict):
    """End of the talk: big thank-you, an invitation to questions, one reference line."""
    b = ctx.b
    t = c.get("title", "Thank you")
    els = [El("text", ctx.m, 1.5, 8.6, 2.2, name="!!title", text=t,
              style={"font": "heading", "size": shrink_to_fit(ctx, t, 8.6, 2.2, b.sizes["big"], spacing=0.95, floor=0.5),
                     "anchor": "b", "spacing": 0.95}),
           ]
    if c.get("subtitle", "Questions?"):                 # "Thank you for your attention" may stand alone
        els.append(El("text", ctx.m, 3.85, 8.6, 0.9, text=c.get("subtitle", "Questions?"),
                      style={"font": "heading", "size": b.sizes["display"] - 16, "color": "ours"}))
    if c.get("reference"):
        els.append(El("text", ctx.m, 5.6, 8.6, 0.9, text=c["reference"],
                      style={"font": "body", "size": b.sizes["small"], "color": "muted", "label": True}))
    if ctx.motif:
        els += ctx.motif(9.6, 1.6, 3.0, 4.0)
    return els


def credits_two(ctx: Ctx, c: dict):
    els = [title(ctx, c["title"])]
    half = (len(c["lines"]) + 1) // 2
    cw = (ctx.cw - 2 * ctx.b.gutter) / 2
    for k, part in enumerate((c["lines"][:half], c["lines"][half:])):
        els.append(body(ctx, ctx.m + k * (cw + 2 * ctx.b.gutter), BODY_Y, cw, BODY_BOTTOM - BODY_Y, "\n".join(part),
                        size=ctx.b.sizes["small"] - 1, para_gap=6, spacing=1.05))
    return els


LIBRARY: dict[str, dict[str, Callable]] = {
    "title": {"title_left": title_left, "title_center": title_center},
    "section": {"section_number": section_number, "section_center": section_center},
    "content": {"content_one": content_one, "content_two": content_two},
    "figure": {"figure_full": figure_full},
    "figure_text": {"figure_left_text_right": figure_left_text_right, "figure_right_text_left": figure_right_text_left,
                    "figure_top_text_bottom": figure_top_text_bottom},
    "comparison": {"comparison_two": comparison_two, "comparison_three": comparison_three},
    "statement": {"statement_sentence": statement_sentence, "statement_number": statement_number},
    "formula": {"formula_center": formula_center},
    "evidence": {"evidence_chart_side": evidence_chart_side, "evidence_chart_full": evidence_chart_full,
                 "evidence_table": evidence_table},
    "diagram": {"diagram_flow": diagram_flow},
    "closing": {"closing_takeaways": closing_takeaways, "closing_thanks": closing_thanks},
    "credits": {"credits_two": credits_two},
}


def catalog(b: Brand) -> dict:
    """Measured word capacity of the main text slots for brand b."""
    m, cw = b.margin, W - 2 * b.margin
    bh = BODY_BOTTOM - BODY_Y
    gut = b.gutter
    cap = lambda fam, size, w, h, bold=False, sp=1.12: fonts.capacity_words(fam, size, w, h, bold, sp)
    hf, hb = b.heading_font, b.heading_bold
    return {
        "title_left.title": cap(hf, b.sizes["display"], 8.4, 2.3, hb, 0.95),
        "any.slide_title(1 line)": cap(hf, b.sizes["title"], cw, TITLE_H, hb, 1.0),
        "content_one.body": cap(b.body_font, b.sizes["body"], 8.6, bh),
        "content_two.col": cap(b.body_font, b.sizes["body"], (cw - 2 * gut) / 2, bh),
        "figure_left_text_right.text": cap(b.body_font, b.sizes["body"] - 1, cw * 0.4 - 2 * gut, bh - 0.2),
        "figure_top_text_bottom.text": cap(b.body_font, b.sizes["body"] - 1, cw, bh - 3.5),
        "comparison_two.card": cap(b.body_font, b.sizes["body"], (cw - 3 * gut) / 2 - 0.6, bh - 0.75 - 0.6, sp=1.1),
        "comparison_three.card": cap(b.body_font, b.sizes["body"] - 1, (cw - 4 * gut) / 3 - 0.52, bh - 0.75 - 0.52, sp=1.1),
        "statement_sentence.text": cap(hf, b.sizes["display"] - 10, W - 2.6, 3.8, hb, 1.05),
        "evidence_chart_side.takeaway": cap(b.body_font, b.sizes["h2"], cw * 0.34 - 2 * gut, 3.6),
        "closing.takeaway(3)": cap(b.body_font, b.sizes["body"], (cw - 4 * gut) / 3, 2.2),
    }
