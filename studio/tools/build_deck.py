"""Stage 8: build a full deck from a content spec with the current choices.

    python tools/build_deck.py fixture/geora_deck.json [--out outputs/8_deck]
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from run_options import current
from sdl import preview, motion as mo
from sdl.assemble import Assembler
from sdl.check import check_deck, report
from sdl.render import render_deck
from sdl.visuals import lowrank_motif, world_backdrop


def resolve_paths(deck):
    for s in deck["slides"]:
        c = s["content"]
        if "image" in c and not Path(c["image"]).is_absolute():
            c["image"] = str(ROOT / c["image"])
    return deck


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("--out", default="outputs/8_deck")
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    c, b, vl, m = current()
    deck = resolve_paths(json.loads((ROOT / a.deck).read_text(encoding="utf-8")))
    out = ROOT / a.out
    name = a.name or Path(a.deck).stem
    world = str(world_backdrop(b, ROOT / "outputs" / "_cache" / f"world_{b.key}.png")) if m.mode == "world" else None
    asm = Assembler(b, vl, m, motif=lowrank_motif(b), world_img=world)
    slides = asm.build(deck)
    issues = check_deck(slides, b)
    pptx = render_deck(slides, b, vl, m, out / f"{name}.pptx")
    pngs = preview.pptx_to_pngs(pptx, out / "png")
    labels = [f"{i + 1} · {s.meta['id']} · {s.meta['variant']}" + (" · M" if s.transition == "morph" else "")
              for i, s in enumerate(slides)]
    preview.contact_sheet(pngs, out / f"{name}_contact.jpg", cols=4, labels=labels,
                          title=f"{deck.get('title', name)} – {b.label} · {vl.label} · {m.label}")
    pairs = [(i - 1, i) for i in range(1, len(slides)) if slides[i].transition == "morph"]
    frames, durs = [], []
    for p in pairs:
        frames += mo.morph_frames(slides, [p], steps=(0, .25, .5, .75, 1))
        durs += [900, 80, 80, 80, 900]
    fp = render_deck(frames, b, vl, m, out / "_frames.pptx")
    fpng = preview.pptx_to_pngs(fp, out / "_frames", width=960)
    preview.gif(fpng, out / f"{name}_morph_sim.gif", ms=durs)
    fp.unlink()
    spec = [{"n": i + 1, "id": s.meta["id"], "variant": s.meta["variant"], "transition": s.transition,
             "builds": len({e.build for e in s.elements if e.build}),
             "morph_names": sorted({e.name for e in s.elements if e.name.startswith("!!")})} for i, s in enumerate(slides)]
    (out / f"{name}_physical.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    txt = f"choices: {json.dumps({k: v for k, v in c.items() if not k.startswith('_')})}\n\n" + report(issues) + \
          "\n\nassembly / critic log:\n" + "\n".join(asm.log)
    (out / f"{name}_check.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
