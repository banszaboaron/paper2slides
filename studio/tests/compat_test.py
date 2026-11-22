"""Stage 1 compatibility test: Morph XML, click-builds, chart, formula, picture, fonts."""
import sys, zipfile
from pathlib import Path
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sdl.brand import STYLES, VISUAL_LANGUAGES, MOTIONS
from sdl.spec import Slide, El
from sdl.render import render_deck
from sdl.check import check_deck, report
from sdl import preview

b, vl, m = STYLES["A_geometric_minimal"], VISUAL_LANGUAGES["1_soft_round"], MOTIONS["1_basic"]
out = ROOT / "outputs" / "1_lab"

s1 = Slide("content", transition="fade").add(
    El("text", 0.6, 0.5, 9, 0.8, name="!!title", text="Compat test: Morph + builds", style={"font": "heading", "size": 34}),
    El("rect", 0.6, 2.0, 2.4, 1.2, name="!!box", text="Box A", style={"fill": "surface", "anchor": "m", "align": "c"}),
    El("rect", 0, 7.38, 13.333 * 1 / 3, 0.12, name="!!progress", role="chrome", style={"fill": "ours"}),
)
s2 = Slide("content", transition="morph").add(
    El("text", 0.6, 0.5, 9, 0.8, name="!!title", text="Compat test: Morph + builds", style={"font": "heading", "size": 34}),
    El("rect", 6.0, 2.6, 5.0, 2.4, name="!!box", text="Box A moved + grown", style={"fill": "ours", "color": "FFFFFF", "anchor": "m", "align": "c"}),
    El("text", 0.6, 2.0, 4.8, 0.6, text="Build 1 (click)", build=1),
    El("text", 0.6, 2.8, 4.8, 0.6, text="Build 2 (click)", build=2),
    El("formula", 0.6, 3.8, 5.0, 1.0, data=r"\mathrm{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right)V", build=3),
    El("rect", 0, 7.38, 13.333 * 2 / 3, 0.12, name="!!progress", role="chrome", style={"fill": "ours"}),
)
s3 = Slide("figure", transition="morph").add(
    El("text", 0.6, 0.5, 9, 0.8, name="!!title", text="Native chart, direct labels", style={"font": "heading", "size": 34}),
    El("chart", 0.8, 1.6, 8.0, 5.2, data={"type": "bar", "categories": ["A", "B", "C", "Ours"],
       "series": [{"name": "score", "values": [61.2, 63.0, 64.1, 68.4]}], "point_roles": ["baseline"] * 3 + ["ours"], "y_min": 50}),
    El("rect", 0, 7.38, 13.333, 0.12, name="!!progress", role="chrome", style={"fill": "ours"}),
)
slides = [s1, s2, s3]
print(report(check_deck(slides, b)))
p = render_deck(slides, b, vl, m, out / "compat_test.pptx")
z = zipfile.ZipFile(p)
for n in z.namelist():
    if n.endswith(".xml"):
        etree.fromstring(z.read(n))
x2 = z.read("ppt/slides/slide2.xml").decode()
assert "p159:morph" in x2 and "<p:timing" in x2 and x2.index("AlternateContent") < x2.index("<p:timing")
assert x2.count('nodeType="clickEffect"') == 3
from pptx import Presentation
Presentation(str(p))
pngs = preview.pptx_to_pngs(p, out / "png")
preview.contact_sheet(pngs, out / "compat_test_contact.jpg", cols=3, title="1 – kompatibilitási teszt (LibreOffice render)")
print("OK", p, len(pngs))
