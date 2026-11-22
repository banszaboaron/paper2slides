"""Alternative build path: add click-builds with COM instead of native XML.

The renderer already writes Fade builds as XML (sdl/xmlfx.py). This script exists
for the A/B test asked by the brief: run it on a copy and compare in PowerPoint.

    python tools\\win\\com_animate.py in.pptx out.pptx builds.json
builds.json: {"3": [["shape name", 1], ["other", 2]]}   # slide -> [name, click step]
"""
import json
import sys
from pathlib import Path

import win32com.client

MSO_ANIM_FADE = 10           # msoAnimEffectFade
TRIGGER_ON_CLICK = 1
TRIGGER_WITH_PREVIOUS = 2


def main(src, dst, spec):
    app = win32com.client.Dispatch("PowerPoint.Application")
    pres = app.Presentations.Open(str(Path(src).resolve()), ReadOnly=False, Untitled=True, WithWindow=False)
    try:
        builds = json.loads(Path(spec).read_text(encoding="utf-8"))
        for sl, items in builds.items():
            slide = pres.Slides(int(sl))
            seq = slide.TimeLine.MainSequence
            last_step = 0
            for name, step in sorted(items, key=lambda x: x[1]):
                trig = TRIGGER_ON_CLICK if step != last_step else TRIGGER_WITH_PREVIOUS
                seq.AddEffect(slide.Shapes(name), MSO_ANIM_FADE, 0, trig)
                last_step = step
        pres.SaveAs(str(Path(dst).resolve()))
    finally:
        pres.Close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
