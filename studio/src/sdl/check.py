"""Deterministic checks on physical slide specs (run before rendering)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import fonts, rules
from .brand import Brand
from .spec import Slide, El, W, H


@dataclass
class Issue:
    slide: int
    level: str      # error | warn | info
    code: str
    msg: str

    def __str__(self):
        return f"[{self.level}] s{self.slide:02d} {self.code}: {self.msg}"


def _lum(hexc: str) -> float:
    c = [int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _resolve(brand: Brand, c, default):
    if not c:
        return default
    return c if re.fullmatch(r"[0-9A-Fa-f]{6}", c) else getattr(brand, c, default)


def _font(brand: Brand, st: dict):
    f = st.get("font", "body")
    fam = {"heading": brand.heading_font, "body": brand.body_font, "mono": brand.mono_font}.get(f, f)
    bold = st.get("bold", brand.heading_bold if f == "heading" else False)
    return fam, bold


def _overlap(a: El, b: El) -> float:
    x = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    y = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return x * y


def words(s: Slide) -> int:
    n = 0
    for e in s.elements:
        if e.kind in ("text", "rect", "ellipse") and e.role == "content" and e.text and not e.style.get("label") \
                and not e.style.get("_ghost"):
            n += len(re.findall(r"\w+", fonts.strip_markup(e.text)))
    return n


def check_deck(slides: list[Slide], brand: Brand) -> list[Issue]:
    out: list[Issue] = []
    for i, s in enumerate(slides, 1):
        bg = _resolve(brand, s.bg, brand.bg)
        names = [e.name for e in s.elements if e.name]
        for n in {n for n in names if names.count(n) > 1}:
            out.append(Issue(i, "error", "dup-name", f"'{n}' is used twice (Morph needs 1:1 names)"))
        nb = len({e.build for e in s.elements if e.build})
        if nb > rules.MAX_BUILDS:
            out.append(Issue(i, "error", "builds", f"{nb} builds > {rules.MAX_BUILDS}"))
        wl = rules.WORD_LIMITS.get(s.kind)
        if wl and words(s) > wl:
            out.append(Issue(i, "warn", "words", f"{words(s)} words > limit {wl} for '{s.kind}'"))
        deco = [e for e in s.elements if e.role == "decoration"]
        if len(deco) > rules.DECORATION_PER_SLIDE:
            out.append(Issue(i, "warn", "decoration", f"{len(deco)} decorative objects > {rules.DECORATION_PER_SLIDE}"))
        for e in s.elements:
            if e.style.get("_ghost"):                 # canvas world: neighbouring frames seen from afar
                continue
            label = e.name or f"{e.kind}@{e.x:.1f},{e.y:.1f}"
            off = e.x < -0.01 or e.y < -0.01 or e.x + e.w > W + 0.01 or e.y + e.h > H + 0.01
            if off and e.kind != "line" and e.role not in ("world",) and not e.style.get("allow_offslide"):
                out.append(Issue(i, "error", "bounds", f"{label} leaves the slide"))
            if e.kind == "image" and e.role not in ("world",) and not e.meaning:
                out.append(Issue(i, "warn", "image-meaning", f"{label} has no 'meaning' (brief #5)"))
            if e.text and e.kind in ("text", "rect", "ellipse"):
                fam0, bold0 = _font(brand, e.style)
                if fonts.known(fam0):
                    from .mathtext import INLINE, typography
                    cmap = fonts.metrics(fam0, bold0).cmap
                    plain = typography(INLINE.sub("", fonts.MARKUP.sub("", e.text)))
                    miss = sorted({ch for ch in plain if ord(ch) not in cmap and ch not in "\n\t "})
                    if miss:
                        out.append(Issue(i, "warn", "glyph", f"{label}: {''.join(miss)} not in {fam0} -> math-font fallback; prefer $...$"))
                if re.search(r"\b[A-Za-z]_[A-Za-z0-9{]", re.sub(r"\$[^$]+\$", "", e.text)):
                    out.append(Issue(i, "error", "raw-math", f"{label}: subscript written as plain text; wrap it in $...$"))
                fam, bold = _font(brand, e.style)
                size = e.style.get("size", brand.sizes["body"])
                pad = e.style.get("pad", 0.0)
                vpad = e.style.get("vpad", pad)
                ok, ratio = fonts.fits(e.text, fam, size, e.w - 2 * pad, e.h - 2 * vpad, bold,
                                       e.style.get("spacing", 1.0) or 1.0, e.style.get("para_gap", 0))
                if not ok:
                    out.append(Issue(i, "error", "overflow", f"{label}: text needs {ratio:.0%} of box height"))
                mn = rules.MIN_FONT_PT.get(e.role, 14)
                if size < mn and e.role != "world":
                    out.append(Issue(i, "warn", "font-size", f"{label}: {size}pt < {mn}pt"))
                under = _resolve(brand, e.style.get("fill"), bg) if e.kind != "text" else bg
                if e.kind != "text" and e.style.get("fill") in (None, "none"):
                    under = bg
                col = _resolve(brand, e.style.get("color"), brand.text)
                cr = contrast(col, under)
                need = rules.MIN_CONTRAST_LARGE if size >= 24 else rules.MIN_CONTRAST
                if e.role in ("decoration", "world"):
                    need = 1.0
                if cr < need:
                    out.append(Issue(i, "warn", "contrast", f"{label}: {cr:.1f}:1 < {need}:1"))
        content = [e for e in s.elements if e.role == "content" and not e.style.get("_ghost") and e.kind in ("text", "chart", "image", "formula")]
        motif = [e for e in s.elements if e.role == "motif"]
        if motif:
            mb = El("rect", min(e.x for e in motif), min(e.y for e in motif),
                    max(e.x + e.w for e in motif) - min(e.x for e in motif),
                    max(e.y + e.h for e in motif) - min(e.y for e in motif))
            for e in content:
                if e.kind == "text" and _overlap(e, mb) > 0.01:
                    out.append(Issue(i, "warn", "motif-overlap", f"{e.name or 'text'} box overlaps the motif"))
        for a in range(len(content)):
            for b in range(a + 1, len(content)):
                ea, eb = content[a], content[b]
                if ea.style.get("allow_overlap") or eb.style.get("allow_overlap"):
                    continue
                ov = _overlap(ea, eb)
                if ov > 0.02 * min(ea.w * ea.h, eb.w * eb.h):
                    out.append(Issue(i, "warn", "overlap", f"{ea.name or ea.kind} overlaps {eb.name or eb.kind}"))
        # Morph pairing lint (transition lives on the target slide)
        if s.transition == "morph" and i > 1:
            prev = {e.name: e for e in slides[i - 2].elements if e.name.startswith("!!")}
            cur = {e.name: e for e in s.elements if e.name.startswith("!!")}
            pairs = set(prev) & set(cur)
            if not pairs:
                out.append(Issue(i, "info", "morph-nopair", "Morph without any '!!' pair (auto matching only)"))
            for n in pairs:
                if n.startswith("!!cv_"):              # canvas world: the camera zooms, by design
                    continue
                a, b = prev[n], cur[n]
                if a.kind != b.kind:
                    out.append(Issue(i, "warn", "morph-kind", f"{n}: {a.kind}->{b.kind} will cross-fade, not morph"))
                if a.kind == "chart":
                    out.append(Issue(i, "info", "morph-chart", f"{n}: charts cross-fade under Morph"))
                if a.kind == "image" and a.w > 0 and b.w > 0:
                    def px_per_in(el):  # inches of slide per full image width (crop-aware)
                        c = el.style.get("crop") or (0, 0, 0, 0)
                        return el.w / max(1e-6, 1 - c[0] - c[2])
                    z = max(px_per_in(b) / px_per_in(a), px_per_in(a) / px_per_in(b))
                    if z > rules.MAX_ZOOM_PER_TRANSITION + 1e-6:
                        out.append(Issue(i, "warn", "zoom", f"{n}: zoom {z:.1f}x > {rules.MAX_ZOOM_PER_TRANSITION}x"))
    return out


def report(issues: list[Issue]) -> str:
    if not issues:
        return "OK – no issues."
    order = {"error": 0, "warn": 1, "info": 2}
    return "\n".join(str(x) for x in sorted(issues, key=lambda x: (order[x.level], x.slide)))


def font_audit(pptx_path, brand: Brand) -> list[Issue]:
    """After rendering: every text run on every slide uses the chosen fonts (or the math font).
    Catches a run that would fall back to the theme font (Calibri) or a font that is not installed."""
    import zipfile
    allowed = {brand.heading_font, brand.body_font, brand.mono_font, "Cambria Math"}
    out: list[Issue] = []
    with zipfile.ZipFile(pptx_path) as z:
        names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                       key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for n in names:
            k = int(re.findall(r"\d+", n)[-1])
            x = z.read(n).decode("utf-8")
            seen = set()
            for r in re.findall(r"<a:r>(.*?)</a:r>", x, re.S):
                text = "".join(re.findall(r"<a:t>(.*?)</a:t>", r, re.S)).strip()
                if not text:
                    continue
                m = re.search(r'<a:latin typeface="([^"]*)"', r)
                fam = m.group(1) if m else "(theme font)"
                if fam not in allowed and fam not in seen:
                    seen.add(fam)
                    out.append(Issue(k, "error", "font", f"“{text[:30]}” uses {fam}, not the chosen "
                                                         f"{brand.heading_font} / {brand.body_font} / {brand.mono_font}"))
            for fam in fonts_missing(allowed - {"Cambria Math"}) if k == 1 else []:
                out.append(Issue(k, "warn", "font-missing", f"{fam} is not available for measuring/installing"))
    return out


def fonts_missing(families) -> list[str]:
    return [f for f in families if not fonts.known(f)]
