"""Stage 7: template library specimen (every variant once) + measured slot capacities."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sdl import templates, preview
from sdl.assemble import Assembler
from sdl.check import check_deck, report
from sdl.fixtures import L, ATTENTION, generic_figure
from sdl.render import render_deck
from sdl.visuals import lowrank_motif
sys.path.insert(0, str(ROOT / "tools"))
from run_options import current

c, b, vl, m = current()
out = ROOT / "outputs" / "7_templates"
fig = str(generic_figure(b, ROOT / "outputs" / "_cache" / f"generic_figure_{b.key}.png"))
wide = str(ROOT / "outputs" / "_cache" / "generic_wide.png")
from PIL import Image
im = Image.open(fig); w = Image.new("RGBA", (im.width * 3, im.height), (0, 0, 0, 0))
for k in range(3): w.paste(im, (k * im.width, 0))
w.save(wide)
chart = {"type": "bar", "categories": ["Base", "LoRA", "PiSSA", "Ours"],
         "series": [{"name": "acc", "values": [51.0, 58.2, 60.5, 63.9]}], "point_roles": ["baseline"] * 3 + ["ours"], "y_min": 40}
line = {"type": "line", "categories": [str(x) for x in range(100, 700, 100)],
        "series": [{"name": "Lorem", "values": [12, 14, 15, 16, 16.5, 17], "role": "baseline"},
                   {"name": "Ours", "values": [14, 17, 19, 21, 22, 22.5], "role": "ours"}], "y_min": 10}
C = {
    "title": {"title": "Lorem ipsum dolor sit amet", "subtitle": "Consectetur adipiscing elit", "authors": "Lorem · Ipsum · Dolor"},
    "section": {"number": "02", "title": "Dolor sit amet"},
    "content": {"title": "Lorem ipsum dolor sit amet", "body": L},
    "figure": {"title": "Lorem ipsum dolor", "image": wide, "meaning": "placeholder", "caption": "Figure 1 · lorem ipsum"},
    "figure_text": {"title": "Lorem ipsum dolor", "image": fig, "meaning": "placeholder", "body": L[:3]},
    "comparison": {"title": "Lorem vs. ipsum", "columns": [{"head": "Lorem", "items": L[:2]}, {"head": "Ipsum", "items": L[1:3]},
                                                         {"head": "Dolor", "items": L[2:], "role": "ours"}]},
    "statement": {"text": "Lorem ipsum ==dolor sit amet==, consectetur adipiscing elit.", "number": "99.5%", "source": "— Lorem et al., 2026"},
    "formula": {"title": "Lorem ipsum", "latex": ATTENTION, "terms": [("Q,K", "lorem ipsum"), ("V", "dolor sit")]},
    "evidence": {"title": "Lorem ipsum dolor sit amet", "chart": chart, "takeaway": "Lorem ==ipsum== dolor sit amet.",
                 "source": "Lorem et al. (2026), Tab. 1",
                 "rows": [["Method", "A", "B", "C"], ["Base", "1.0", "2.0", "3.0"], ["Lorem", "1.5", "2.2", "3.1"], ["Ours", "1.9", "2.6", "3.4"]],
                 "ours_rows": [3]},
    "diagram": {"title": "Lorem ipsum dolor", "steps": ["Lorem", "Ipsum", {"text": "Dolor", "role": "ours"}, "Sit"], "caption": L[0]},
    "closing": {"message": "Lorem ipsum dolor sit amet.", "takeaways": [L[0], L[1], L[2]]},
    "credits": {"title": "Credits", "lines": [f"Lorem ipsum {i} · dolor sit amet" for i in range(14)]},
}
deck = {"slides": []}
for kind, vs in templates.LIBRARY.items():
    for v in vs:
        cc = dict(C[kind])
        if v == "evidence_chart_full":
            cc["chart"] = line
        deck["slides"].append({"id": v, "type": kind, "variant": v, "section": "Lorem", "content": cc})
asm = Assembler(b, vl, m, motif=lowrank_motif(b))
slides = asm.build(deck)
issues = check_deck(slides, b)
p = render_deck(slides, b, vl, m, out / "template_library.pptx")
pngs = preview.pptx_to_pngs(p, out / "png")
preview.contact_sheet(pngs, out / "template_library_contact.jpg", cols=5,
                      labels=[f"{i + 1} · {s.meta['variant']}" for i, s in enumerate(slides)],
                      title=f"Template library – {b.label}, {vl.label}")
cat = {"brand": b.key, "visual_language": vl.key, "word_capacity": templates.catalog(b),
       "variants": {k: list(v) for k, v in templates.LIBRARY.items()}}
(out / "catalog.json").write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
(out / "check.txt").write_text(report(issues) + "\n\n" + "\n".join(asm.log), encoding="utf-8")
print(json.dumps(cat["word_capacity"], indent=1)); print(report(issues))
