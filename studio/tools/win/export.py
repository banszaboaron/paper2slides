"""PowerPoint COM check + export (run on the laptop, not in the cloud).

Usage (from slide-design-lab):
    python tools\\win\\export.py outputs\\8_deck\\geora_deck.pptx --png --pdf --mp4

Safety: starts its own PowerPoint presentation object with WithWindow=False and closes
only that object. It never touches presentations that are already open, and it does
not call Application.Quit() if PowerPoint was already running.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pythoncom
import win32com.client


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--mp4", action="store_true")
    ap.add_argument("--width", type=int, default=1920)
    a = ap.parse_args()

    src = Path(a.pptx).resolve()
    out = src.parent / (src.stem + "_ppt")
    out.mkdir(exist_ok=True)
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    was_open = app.Presentations.Count
    report = {"file": str(src), "opened": False}
    pres = app.Presentations.Open(str(src), ReadOnly=True, Untitled=False, WithWindow=False)
    try:
        report["opened"] = True
        report["slides"] = pres.Slides.Count
        report["transitions"] = []
        for s in pres.Slides:
            t = s.SlideShowTransition
            report["transitions"].append({"slide": s.SlideIndex, "entry_effect": t.EntryEffect,
                                          "duration": round(t.Duration, 2),
                                          "builds": s.TimeLine.MainSequence.Count})
        fonts = sorted({pres.Fonts.Item(i).Name for i in range(1, pres.Fonts.Count + 1)})
        report["fonts"] = fonts
        if a.png:
            h = int(a.width * pres.PageSetup.SlideHeight / pres.PageSetup.SlideWidth)
            for s in pres.Slides:
                s.Export(str(out / f"slide{s.SlideIndex:02d}.png"), "PNG", a.width, h)
        if a.pdf:
            pres.SaveAs(str(out / (src.stem + ".pdf")), 32)  # ppSaveAsPDF
        if a.mp4:
            # CreateVideo(FileName, UseTimingsAndNarrations, DefaultSlideDuration, VertResolution, FramesPerSecond, Quality)
            target = out / (src.stem + ".mp4")
            pres.CreateVideo(str(target), False, 3, 1080, 30, 85)
            while pres.CreateVideoStatus in (1, 2):  # queued / in progress
                time.sleep(2)
            report["video_status"] = pres.CreateVideoStatus  # 3 = done
    finally:
        pres.Close()
        if was_open == 0 and app.Presentations.Count == 0:
            app.Quit()
    (out / "com_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # Compare entry_effect of Morph slides with slide 1 (fade): identical values everywhere = fallback was used.


if __name__ == "__main__":
    sys.exit(main())
