"""The paper's own figures, cropped from the PDF so the content agent can put them on slides.

Most papers draw their plots as vectors, so looking for embedded images is not enough. We find every
caption ("Figure 3:", "Fig. 3.") and take the graphics (images + vector drawings + their small labels)
directly above it, in the caption's column. Big embedded images without a caption are kept as a fallback.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CAP = re.compile(r"^\s*(Figure|Fig\.)\s*(\d+)\s*[:.|]", re.I)


def _union(rects):
    import pymupdf
    u = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        u |= r
    return u


def extract(pdf: str | Path, out_dir: Path, zoom: float = 2.6) -> list[dict]:
    import pymupdf
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    found: dict[str, dict] = {}
    for pno, page in enumerate(doc):
        W, H = page.rect.width, page.rect.height
        d = page.get_text("dict")
        blocks = [b for b in d["blocks"] if b.get("type") == 0]
        texts = []
        for b in blocks:
            t = " ".join(s["text"] for ln in b["lines"] for s in ln["spans"]).strip()
            texts.append((pymupdf.Rect(b["bbox"]), t))
        graphics = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
        for dr in page.get_drawings():
            r = pymupdf.Rect(dr["rect"])
            if r.width > 3 or r.height > 3:
                graphics.append(r)
        graphics = [g for g in graphics if g.width < W * 0.98 or g.height < H * 0.9]   # page frames
        for rect, t in texts:
            m = CAP.match(t)
            if not m:
                continue
            num = m.group(2)
            fid = f"fig{num}"
            if fid in found:
                continue
            wide = rect.width > W * 0.55
            x0, x1 = (W * 0.04, W * 0.96) if wide else (rect.x0 - 25, rect.x1 + 25)
            band = [g for g in graphics if g.y1 <= rect.y0 + 3 and g.y0 >= rect.y0 - H * 0.8
                    and g.x1 > x0 and g.x0 < x1]
            seed = [g for g in band if rect.y0 - g.y1 < 40]
            if not seed:
                continue
            u = _union(seed)
            grew = True
            while grew:
                grew = False
                for g in band:
                    if g in seed:
                        continue
                    if g.y1 >= u.y0 - 22 and g.x1 > u.x0 - 10 and g.x0 < u.x1 + 10:
                        seed.append(g)
                        u |= g
                        grew = True
            for tr, tt in texts:                       # axis labels and legends inside the graphic
                if tr == rect or CAP.match(tt):
                    continue
                inside = tr.y0 >= u.y0 - 4 and tr.y1 <= rect.y0 + 1
                title = u.y0 - 8 <= tr.y1 <= u.y0 + 12 and tr.y0 >= u.y0 - 40 and tr.height < 30  # panel titles just above
                if (inside or title) and tr.x0 >= u.x0 - 30 and tr.x1 <= u.x1 + 30 and tr.height < 60:
                    u |= tr
                elif u.x0 - 40 <= tr.x0 and tr.x1 <= u.x0 + 12 and tr.width < 30 and tr.y0 >= u.y0 - 4 \
                        and tr.y1 <= rect.y0 + 1:            # a rotated y-axis label just left of the plot
                    u |= tr
            u = (u + (-4, -4, 4, 4)) & page.rect
            if u.width < 80 or u.height < 60:
                continue
            png = out_dir / f"{fid}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=u).save(str(png))
            found[fid] = {"id": fid, "label": f"Figure {num}", "caption": t[:400], "page": pno + 1,
                          "path": str(png), "aspect": round(u.width / u.height, 2)}
    if not found:                                       # fallback: large embedded pictures
        k = 0
        for pno, page in enumerate(doc):
            for info in page.get_image_info():
                import pymupdf as _p
                r = _p.Rect(info["bbox"])
                if r.width < 180 or r.height < 120:
                    continue
                k += 1
                png = out_dir / f"img{k}.png"
                page.get_pixmap(matrix=_p.Matrix(zoom, zoom), clip=r).save(str(png))
                found[f"img{k}"] = {"id": f"img{k}", "label": f"Image {k}", "caption": "", "page": pno + 1,
                                    "path": str(png), "aspect": round(r.width / r.height, 2)}
    figs = sorted(found.values(), key=lambda f: (f["page"], f["id"]))
    (out_dir / "figures.json").write_text(json.dumps(figs, indent=1, ensure_ascii=False), encoding="utf-8")
    return figs
