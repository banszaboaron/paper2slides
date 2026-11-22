"""Offline tests for the portfolio fixes: zoom crops never cut ink; numbers that are names are not quantities."""
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio" / "src"))


def _two_panels(path):
    """Two dark panels on white with a gap between them (x 0-45 % and 55-100 %), a label across the top."""
    im = Image.new("L", (1000, 400), 255)
    d = ImageDraw.Draw(im)
    d.rectangle([20, 60, 450, 380], outline=0, width=3)
    d.rectangle([550, 60, 980, 380], outline=0, width=3)
    d.rectangle([600, 10, 900, 40], fill=0)        # a title that a careless crop would slice
    im.save(path)


def test_snap_region_moves_edges_off_ink(tmp_path):
    from sdl.motion import snap_region
    p = tmp_path / "fig.png"
    _two_panels(p)
    # the right edge lies on the second panel's frame; the left edge cuts the title bar
    x0, y0, x1, y1 = snap_region(p, (0.0, 0.0, 0.551, 1.0))
    im = Image.open(p)
    edge = round(x1 * 1000) - 1                         # last column inside the crop
    ink = sum(im.getpixel((edge, y)) < 200 for y in range(400)) / 400
    assert ink <= 0.015, "the crop must not end on a line of ink (crossing thin rules is fine)"
    assert 0.452 <= x1 <= 0.56, "it moves to the nearest blank column, not into the next panel"
    x0, y0, x1, y1 = snap_region(p, (0.7, 0.0, 1.0, 1.0))
    assert x0 <= 0.6, "the title bar (x 60-90 %) must be kept whole"


def test_snap_region_keeps_clean_and_border_edges(tmp_path):
    from sdl.motion import snap_region
    p = tmp_path / "fig.png"
    _two_panels(p)
    assert snap_region(p, (0.0, 0.0, 1.0, 1.0)) == (0.0, 0.0, 1.0, 1.0)
    x0, _, x1, _ = snap_region(p, (0.5, 0.0, 1.0, 1.0))          # 50 % is in the blank gap already
    assert x0 == pytest.approx(0.5, abs=0.002) and x1 == 1.0


def test_eval_skips_numbers_inside_names():
    sys.path.insert(0, str(ROOT))
    from eval.evaluate import grounding
    spec = {"slides": [{"id": "s1", "type": "content",
                        "content": {"title": "o1-0912 at cons@64", "body": ["accuracy 86.7% vs 50.3%"]}}]}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        g = grounding(spec, "accuracy rose to 86.7% on MATH", Path(td) / "n.csv")
    assert g["numbers_checked"] == 2                 # 86.7 and 50.3, not 0912 or 64
    assert g["not_in_source"] == 1 and g["not_in_source_list"][0]["number"] == "50.3%"
