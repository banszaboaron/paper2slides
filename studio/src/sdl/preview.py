"""Previews: PowerPoint COM (laptop) or LibreOffice (cloud) -> PNG -> contact sheet / GIF.

LibreOffice ignores Morph and click-builds, so cloud previews show the final state of
each physical slide. Morph motion is approximated by `motion.interpolate` frames
(see tools/morph_frames). The real check is tools/win/export.py on the laptop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _soffice() -> str | None:
    exe = shutil.which("soffice") or shutil.which("soffice.exe")
    if exe:
        return exe
    for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if Path(c).exists():
            return c
    return None


def _has_powerpoint() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def backend() -> str:
    """SDL_RENDERER=auto|powerpoint|libreoffice. auto: PowerPoint on Windows if pywin32 is installed."""
    want = os.environ.get("SDL_RENDERER", "auto").lower()
    if want == "powerpoint" or (want == "auto" and _has_powerpoint()):
        return "powerpoint"
    if _soffice():
        return "libreoffice"
    raise RuntimeError("no renderer: install pywin32 (PowerPoint) or LibreOffice")


def _pdf_to_pngs(pdf: Path, out_dir: Path, stem: str, width: int) -> list[Path]:
    import pymupdf
    doc = pymupdf.open(str(pdf))
    out = []
    for i, page in enumerate(doc, 1):
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        f = out_dir / f"{stem}-{i:02d}.png"
        pix.save(str(f))
        out.append(f)
    return out


def _libreoffice(pptx: Path, out_dir: Path, width: int) -> list[Path]:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([_soffice(), "--headless", "--convert-to", "pdf", "--outdir", td, str(pptx)],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        pdf = Path(td) / (pptx.stem + ".pdf")
        pngs = _pdf_to_pngs(pdf, out_dir, pptx.stem, width)
        shutil.copy(pdf, out_dir / (pptx.stem + ".pdf"))
    return pngs


_PPT = {"app": None, "thread": None, "quit": False}


def _ppt_app():
    """One PowerPoint instance per process and thread, reused for every render.

    Quitting PowerPoint after each deck and dispatching again right away races with the
    shutting-down server (RPC_E_DISCONNECTED / RPC server unavailable), so we keep it and
    quit once at process exit – only if this process started it."""
    import atexit
    import threading
    import pythoncom
    import win32com.client
    me = threading.get_ident()
    if _PPT["app"] is not None and _PPT["thread"] == me:
        try:
            _PPT["app"].Presentations.Count          # still alive?
            return _PPT["app"]
        except Exception:
            _PPT["app"] = None
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    first = _PPT["thread"] is None
    _PPT.update(app=app, thread=me)
    if first:
        _PPT["quit"] = app.Presentations.Count == 0

        def _bye():
            try:
                if _PPT["quit"] and _PPT["app"] is not None and _PPT["app"].Presentations.Count == 0:
                    _PPT["app"].Quit()
            except Exception:
                pass
        atexit.register(_bye)
    return app


def _powerpoint(pptx: Path, out_dir: Path, width: int) -> list[Path]:
    """Real PowerPoint render (laptop). Opens its own window-less copy; never touches open decks."""
    last = None
    for attempt in range(3):
        try:
            app = _ppt_app()
            pres = app.Presentations.Open(str(pptx.resolve()), ReadOnly=True, Untitled=False, WithWindow=False)
            try:
                h = int(width * pres.PageSetup.SlideHeight / pres.PageSetup.SlideWidth)
                out = []
                for sl in pres.Slides:
                    f = out_dir / f"{pptx.stem}-{sl.SlideIndex:02d}.png"
                    sl.Export(str(f.resolve()), "PNG", width, h)
                    out.append(f)
            finally:
                pres.Close()
            return out
        except Exception as e:                          # COM hiccup: reconnect and retry
            last = e
            _PPT["app"] = None
            import time
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"PowerPoint render failed: {last}")


def pptx_to_pngs(pptx: Path, out_dir: Path, width: int = 1600) -> list[Path]:
    pptx, out_dir = Path(pptx), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{pptx.stem}-*.png"):
        old.unlink()
    pngs = _powerpoint(pptx, out_dir, width) if backend() == "powerpoint" else _libreoffice(pptx, out_dir, width)
    return sorted(pngs)


def contact_sheet(pngs: list[Path], out: Path, cols: int = 4, thumb_w: int = 480, labels: list[str] | None = None,
                  title: str | None = None, bg=(236, 236, 236)) -> Path:
    ims = [Image.open(p).convert("RGB") for p in pngs]
    th = int(thumb_w * ims[0].height / ims[0].width)
    pad, lab_h = 16, 26
    top = 50 if title else 0
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (thumb_w + pad) + pad, top + rows * (th + lab_h + pad) + pad), bg)
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(str(Path(__file__).resolve().parents[2] / "fonts" / "Inter-Regular.ttf"), 15)
        ft = ImageFont.truetype(str(Path(__file__).resolve().parents[2] / "fonts" / "Inter-SemiBold.ttf"), 24)
    except OSError:
        f = ft = ImageFont.load_default()
    if title:
        d.text((pad, 14), title, fill=(20, 20, 20), font=ft)
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        x, y = pad + c * (thumb_w + pad), top + pad + r * (th + lab_h + pad)
        sheet.paste(im.resize((thumb_w, th), Image.LANCZOS), (x, y))
        d.rectangle([x - 1, y - 1, x + thumb_w, y + th], outline=(190, 190, 190))
        lab = labels[i] if labels and i < len(labels) else f"{i + 1}"
        d.text((x, y + th + 5), lab, fill=(40, 40, 40), font=f)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def gif(pngs: list[Path], out: Path, ms: list[int] | int = 120, width: int = 960) -> Path:
    frames = []
    for p in pngs:
        im = Image.open(p).convert("RGB")
        frames.append(im.resize((width, int(width * im.height / im.width)), Image.LANCZOS))
    durs = ms if isinstance(ms, list) else [ms] * len(frames)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durs, loop=0, optimize=True)
    return out
