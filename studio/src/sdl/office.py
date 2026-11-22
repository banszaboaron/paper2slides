"""PowerPoint COM on the laptop: final export (PNG / PDF / MP4) and a report of what PowerPoint sees.

Safety: opens its own window-less copy of the deck, closes only that, and quits PowerPoint
only if it was not running before.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def available() -> dict:
    if os.name != "nt":
        return {"ok": False, "reason": "PowerPoint COM only works on Windows (laptop)"}
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "pip install pywin32"}
    return {"ok": True, "reason": ""}


def export(pptx: str, png: bool = True, pdf: bool = True, mp4: bool = False, width: int = 1920,
           seconds_per_slide: int = 3, height: int = 1080) -> dict:
    av = available()
    if not av["ok"]:
        raise RuntimeError(av["reason"])
    from .preview import _ppt_app
    src = Path(pptx).resolve()
    out = src.parent / (src.stem + "_ppt")
    out.mkdir(exist_ok=True)
    app = _ppt_app()
    pres = app.Presentations.Open(str(src), ReadOnly=True, Untitled=False, WithWindow=False)
    rep = {"file": str(src), "out_dir": str(out), "files": []}
    try:
        rep["slides"] = pres.Slides.Count
        rep["transitions"] = [{"slide": s.SlideIndex, "entry_effect": s.SlideShowTransition.EntryEffect,
                               "duration": round(s.SlideShowTransition.Duration, 2),
                               "builds": s.TimeLine.MainSequence.Count} for s in pres.Slides]
        rep["fonts"] = sorted({pres.Fonts.Item(i).Name for i in range(1, pres.Fonts.Count + 1)})
        if png:
            h = int(width * pres.PageSetup.SlideHeight / pres.PageSetup.SlideWidth)
            for s in pres.Slides:
                f = out / f"slide{s.SlideIndex:02d}.png"
                s.Export(str(f), "PNG", width, h)
                rep["files"].append(str(f))
        if pdf:
            f = out / (src.stem + ".pdf")
            pres.SaveAs(str(f), 32)                      # ppSaveAsPDF
            rep["files"].append(str(f))
        if mp4:
            f = out / (src.stem + ".mp4")
            pres.CreateVideo(str(f), False, seconds_per_slide, height, 30, 80)
            t0 = time.time()
            while pres.CreateVideoStatus in (1, 2) and time.time() - t0 < 900:   # queued / in progress
                time.sleep(1)
            rep["video_status"] = pres.CreateVideoStatus  # 3 = done
            if f.exists():
                rep["files"].append(str(f))
    finally:
        pres.Close()
    missing = [f for f in rep["fonts"] if not _installed(f)]
    rep["fonts_not_installed"] = missing
    (out / "com_report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return rep


def add_3d_models(pptx: str) -> int:
    """Swap the '!!world3d' pictures for the real glTF model, rotated per station (Morph rotates it).

    Reads <pptx>.world3d.json written by the renderer: [{"slide": n, "glb": path, "rot_y": deg}, ...]."""
    from .preview import _ppt_app
    src = Path(pptx).resolve()
    side = Path(str(src) + ".world3d.json")
    items = json.loads(side.read_text(encoding="utf-8"))
    app = _ppt_app()
    pres = app.Presentations.Open(str(src), ReadOnly=False, Untitled=False, WithWindow=False)
    n = 0
    try:
        for it in items:
            sl = pres.Slides(it["slide"])
            old = next((sh for sh in sl.Shapes if sh.Name == "!!world3d"), None)
            if old is None:
                continue
            left, top, w, h = old.Left, old.Top, old.Width, old.Height
            z = old.ZOrderPosition
            old.Delete()
            m = sl.Shapes.Add3DModel(str(Path(it["glb"]).resolve()), False, True, left, top, w, h)
            m.Name = "!!world3d"
            m.Model3D.RotationY = float(it["rot_y"])
            m.Model3D.RotationX = 12.0
            while m.ZOrderPosition > z:                  # back to where the picture was (under the veil)
                m.ZOrder(3)                              # msoSendBackward
            n += 1
        pres.Save()
    finally:
        pres.Close()
    side.rename(side.with_suffix(".done.json"))
    return n


def _installed(family: str) -> bool:
    """Registry value names look like 'Inter (TrueType)' or 'Cambria & Cambria Math (TrueType)'."""
    try:
        import winreg
    except ImportError:
        return True
    key = family.split(" SemiBold")[0].lower()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
                i = 0
                while True:
                    name, _, _ = winreg.EnumValue(k, i)
                    faces = [f.strip().lower() for f in name.split("(")[0].split("&")]
                    if any(f == key or f.startswith(key + " ") for f in faces):
                        return True
                    i += 1
        except OSError:
            continue
    return False


def open_file(path: str) -> dict:
    """Open a file (pptx, pdf, folder) with its default Windows app."""
    if os.name != "nt":
        raise RuntimeError("only on Windows")
    p = Path(path).resolve()
    if not p.exists():
        raise RuntimeError(f"not found: {p}")
    os.startfile(str(p))
    return {"opened": str(p)}
