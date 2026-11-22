"""Paper ingestion with PyMuPDF: text + figure crops (PNG) located via their captions.

Figure cropping is heuristic: the region between the last body-text paragraph above
a "Figure N" caption and the caption itself. Crops are saved at `dpi` and should be
eyeballed (the outline HITL step shows them).
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

CAPTION = re.compile(r"^\s*(Figure|Fig\.)\s*(\d+)\s*[:.|]", re.I)
TABLE_CAPTION = re.compile(r"^\s*Table\s*(\d+)\s*[:.|]", re.I)


def extract_text(pdf: Path, max_chars: int = 120_000) -> str:
    with fitz.open(pdf) as doc:
        text = "\n".join(p.get_text("text") for p in doc)
    return text[:max_chars]


def extract_metadata(pdf: Path) -> dict:
    with fitz.open(pdf) as doc:
        meta = doc.metadata or {}
        first = doc[0].get_text("text")[:1500] if len(doc) else ""
        return {"title": meta.get("title") or "", "author": meta.get("author") or "",
                "pages": len(doc), "first_page": first}


def extract_figures(pdf: Path, out_dir: Path, dpi: int = 220) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    figs: list[dict] = []
    seen: set[str] = set()
    with fitz.open(pdf) as doc:
        for pno, page in enumerate(doc):
            pw, ph = page.rect.width, page.rect.height
            blocks = [b for b in page.get_text("blocks") if b[6] == 0]  # text blocks
            for b in blocks:
                x0, y0, x1, y1, txt = b[:5]
                m, kind = CAPTION.match(txt), "fig"
                if not m:
                    m, kind = TABLE_CAPTION.match(txt), "tab"
                if not m:
                    continue
                num = m.group(m.lastindex)
                fid = f"{kind}{num}"
                if fid in seen:
                    continue
                full_width = (x1 - x0) > 0.55 * pw
                cx0, cx1 = (36, pw - 36) if full_width else (x0 - 4, x1 + 4)
                if kind == "fig":   # figure sits above its caption
                    above = [bb for bb in blocks if bb[3] <= y0 - 2 and len(bb[4]) > 250
                             and bb[2] > cx0 and bb[0] < cx1]
                    top = max([bb[3] for bb in above], default=36)
                    clip = fitz.Rect(cx0, top + 2, cx1, y0 - 1)
                else:               # table sits below its caption
                    below = [bb for bb in blocks if bb[1] >= y1 + 2 and len(bb[4]) > 250
                             and bb[2] > cx0 and bb[0] < cx1]
                    bottom = min([bb[1] for bb in below], default=ph - 36)
                    clip = fitz.Rect(cx0, y1 + 1, cx1, bottom - 2)
                if clip.height < 40 or clip.width < 80:
                    continue
                path = out_dir / f"{fid}.png"
                page.get_pixmap(clip=clip, dpi=dpi).save(path)
                seen.add(fid)
                figs.append({"id": fid, "page": pno + 1, "path": str(path),
                             "caption": " ".join(txt.split())[:600],
                             "size_px": [int(clip.width * dpi / 72), int(clip.height * dpi / 72)]})
    return figs
