"""Generate all 4 options of a ★ stage with real renders (autopilot keeps all of them).

    python tools/run_options.py --stage style|brand|visual|motion [--renderer libreoffice|powerpoint]
Choices of earlier stages are read from docs/choices.json (edit it to pick another option, then rerun).
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sdl.brand import STYLES, VISUAL_LANGUAGES, MOTIONS, brand_variants
from sdl.assemble import Assembler
from sdl.render import render_deck
from sdl.check import check_deck, report
from sdl.fixtures import preview_deck
from sdl.visuals import style_motif, world_backdrop
from sdl import preview, motion as mo

CHOICES = ROOT / "docs" / "choices.json"
STAGE_DIR = {"style": "4_launch_brand/options/0_main_style", "brand": "4_launch_brand/options/1_brand",
             "visual": "5_visual_language/options", "motion": "6_motion/options"}


def current():
    c = json.loads(CHOICES.read_text(encoding="utf-8"))
    style = STYLES[c["style"]]
    brand = brand_variants(style)[c["brand"]] if c.get("brand") else style
    vl = VISUAL_LANGUAGES[c.get("visual_language") or "1_soft_round"]
    mot = MOTIONS[c.get("motion") or "1_basic"]
    return c, brand, vl, mot


def build_one(out_dir, brand, vl, mot, full, ids=None, gif=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = ROOT / "outputs" / "_cache"
    deck = preview_deck(brand, assets, full=full)
    if ids:
        deck["slides"] = [s for s in deck["slides"] if s["id"] in ids]
    world = str(world_backdrop(brand, assets / f"world_{brand.key}.png")) if mot.mode == "world" else None
    asm = Assembler(brand, vl, mot, motif=style_motif(brand), world_img=world)
    slides = asm.build(deck)
    issues = check_deck(slides, brand)
    pptx = render_deck(slides, brand, vl, mot, out_dir / "preview.pptx")
    pngs = preview.pptx_to_pngs(pptx, out_dir / "png")
    labels = [f"{i + 1} · {s.meta.get('variant')}" + (" · MORPH" if s.transition == "morph" else "") for i, s in enumerate(slides)]
    (out_dir / "check.txt").write_text(report(issues) + "\n\n" + "\n".join(asm.log), encoding="utf-8")
    sheet = preview.contact_sheet(pngs, out_dir / "contact.jpg", cols=4 if len(pngs) > 3 else len(pngs), labels=labels)
    if gif:
        pairs = [(i - 1, i) for i in range(1, len(slides)) if slides[i].transition == "morph"]
        frames = []
        durs = []
        for a, b in pairs:
            fr = mo.morph_frames(slides, [(a, b)], steps=(0, .2, .4, .6, .8, 1))
            frames += fr
            durs += [700] + [90] * 4 + [700]
        if frames:
            fp = render_deck(frames, brand, vl, mot, out_dir / "_frames.pptx")
            fpng = preview.pptx_to_pngs(fp, out_dir / "_frames", width=960)
            preview.gif(fpng, out_dir / "morph_sim.gif", ms=durs)
            fp.unlink()
    return sheet, issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=list(STAGE_DIR))
    a = ap.parse_args()
    c, brand, vl, mot = current()
    base = ROOT / "outputs" / STAGE_DIR[a.stage]
    sheets = []
    if a.stage == "style":
        for k, st in STYLES.items():
            sheets.append((k, *build_one(base / k, st, vl, MOTIONS["1_basic"], False)))
    elif a.stage == "brand":
        for k, bv in brand_variants(STYLES[c["style"]]).items():
            sheets.append((k, *build_one(base / k, bv, vl, MOTIONS["1_basic"], False)))
    elif a.stage == "visual":
        for k, v in VISUAL_LANGUAGES.items():
            sheets.append((k, *build_one(base / k, brand, v, MOTIONS["1_basic"], True,
                                         ids=["diagram", "compare", "figtext", "formula"])))
    else:
        for k, m in MOTIONS.items():
            sheets.append((k, *build_one(base / k, brand, vl, m, True, gif=m.mode != "none")))
    overview = preview.contact_sheet([s for _, s, _ in sheets], base / "_overview.jpg", cols=2, thumb_w=900,
                                     labels=[f"{k}  ({sum(i.level == 'error' for i in iss)} errors, "
                                             f"{sum(i.level == 'warn' for i in iss)} figy.)" for k, _, iss in sheets],
                                     title=f"★ {a.stage}: 4 options")
    print(overview)


if __name__ == "__main__":
    main()
