"""Design workflow (v2): paper -> theme -> kick-off -> ★ palette -> ★ type -> ★ graphics -> ★ visual
-> ★ layout (main style, lorem slides) -> slide spec (content team) -> ★ motion (the real deck) -> deck.

The selection UI (webapp/) drives the same steps through the MCP servers. Every ★ stage:
the design agent writes options/<stage>.json (4 options), then

    python tools/workflow.py validate <session> <stage>   # readability, real fonts, diversity
    python tools/workflow.py render   <session> <stage>   # board data / PowerPoint renders / videos
    python tools/workflow.py choose   <session> <stage> <key> [--patch '{"ours": "1F5EFF"}']

Other commands:
    init <session> --paper paper.pdf        extract text + metadata into sessions/<session>/paper/
    kickoff <session> --slides 13 [--density tight|medium|dense | --max-chars 270] [--direction academic|bold|tech|minimal]
                                            record the kick-off answers + per-type character limits (limits.json)
    spec <session> --file deck.json [--placeholder]
                                            import + check the content team's slide spec (re-run when the real
                                            spec replaces a placeholder; the ★ choices are kept)
    build <session>                         the full deck with the chosen tokens -> sessions/<s>/deck/
    templates <session>                     (optional) lorem template library with the chosen tokens
    status <session>                        what is done / what is next
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sdl import service as svc, content, templates, fontfetch, preview      # noqa: E402
from sdl.assemble import Assembler                                            # noqa: E402
from sdl.check import check_deck, report                                      # noqa: E402
from sdl.fixtures import L, ATTENTION, generic_figure                         # noqa: E402
from sdl.render import render_deck                                            # noqa: E402
from sdl.visuals import style_motif                                           # noqa: E402


def _print_check(res: dict) -> int:
    for w in res.get("warnings", []):
        print("[warn]", w)
    for e in res.get("errors", []):
        print("[error]", e)
    return 0 if res.get("ok", True) else 1


def cmd_init(a):
    r = svc.ingest_paper(a.session, a.paper)
    fontfetch.install_for_preview()
    print(json.dumps(r["paper"], indent=1, ensure_ascii=False))
    print(f"\nPaper text: {r['text']}\nNext: theme agent writes sessions/{a.session}/brief.json, then `kickoff`.")


def cmd_kickoff(a):
    if svc.get_brief(a.session) is None:
        print(f"warning: sessions/{a.session}/brief.json missing – the theme agent should write it first")
    r = svc.kickoff(a.session, a.slides, a.minutes, a.density, a.max_chars, a.notes or "", a.direction or "")
    print(json.dumps(r["kickoff"], indent=1, ensure_ascii=False))
    print(f"\nLimits for the content team: {r['limits_file']}")


def cmd_spec(a):
    r = svc.import_spec(a.session, a.file, a.placeholder)
    _result(r)                                       # an MCP server reads the verdict, rejected or not
    code = _print_check(r)
    if not r["imported"]:
        print(f"{len(r['errors'])} error(s): the spec was NOT imported – fix {a.file} and run `spec` again")
        return 0 if os.environ.get("SDL_RESULT") else 1
    print(f"OK: {r['slides']} slides ({', '.join(f'{k} {v}' for k, v in r['kinds'].items())})"
          + (" – PLACEHOLDER, replace with the team's spec later" if a.placeholder else ""))
    if r["choices_kept"]:
        print("★ choices are kept; previews rendered before this spec are stale.")
    return code


def cmd_validate(a):
    r = svc.validate_options(a.session, a.stage)
    code = _print_check(r)
    print("OK" if code == 0 else f"{len(r['errors'])} error(s): fix options/{a.stage}.json and validate again")
    return code


def _result(r: dict):
    """When run as a child of an MCP server, hand the result back through a JSON file."""
    out = os.environ.get("SDL_RESULT")
    if out:
        Path(out).write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")


def _log(m: str):
    print("  ..", m, flush=True)


def cmd_render(a):
    r = svc.render_options(a.session, a.stage, log=_log)
    _result(r)
    for k, o in enumerate(r["options"], 1):
        extra = o.get("video") or o.get("gif") or o.get("contact") or ""
        print(f"{k}. {o['key']} – {o['label']}" + (f": {extra}" if extra else ""))
        if any(i["code"] == "overflow" for i in o.get("fit", [])):
            print(f"[fit] {o['key']}: text does not fit with this option -> see check.txt")


def cmd_choose(a):
    r = svc.choose(a.session, a.stage, a.key, json.loads(a.patch) if a.patch else None)
    print(f"chosen {a.stage} = {a.key}" + (f" with {r['patch']}" if r["patch"] else ""))


def cmd_build(a):
    r = svc.build(a.session, a.name, log=_log, video=a.video)
    _result(r)
    print(r["pptx"])
    print(r["contact"])
    for i in r["issues"]:
        print(f"[{i['level']}] s{i['slide']:02d} {i['code']}: {i['msg']}")


def cmd_fixes(a):
    """Accept the visual judge's layout fixes (only those that keep every visible word)."""
    r = svc.apply_fixes(a.session, a.target)
    _result(r)
    for x in r["applied"]:
        print("applied:", x)
    for x in r.get("rejected", []):
        print("rejected:", x)


def cmd_refine(a):
    """Polish step: the change requests (JSON file {requests, general}) -> a new spec version."""
    from sdl import agents
    req = json.loads(Path(a.requests).read_text(encoding="utf-8"))
    r = agents.logged(agents.refine_deck)(a.session, req.get("requests") or [], req.get("general") or "", log=_log)
    _result(r)
    for s in r["summary"]:
        print(s)


def cmd_templates(a):
    st = svc.load_state(a.session)
    if "motion" not in st["choices"]:
        sys.exit("finish the ★ stages first (style, brand, visual, motion)")
    fontfetch.install_for_preview()
    b, vl, m = svc.tokens(a.session)
    out = svc.sdir(a.session) / "templates"
    cache = svc.ROOT / "outputs" / "_cache"
    fig = str(generic_figure(b, cache / f"generic_figure_{b.key}.png"))
    from PIL import Image
    im = Image.open(fig)
    wide_img = Image.new("RGBA", (im.width * 3, im.height), (0, 0, 0, 0))
    for k in range(3):
        wide_img.paste(im, (k * im.width, 0))
    wide = str(cache / f"generic_wide_{b.key}.png")
    wide_img.save(wide)
    chart = {"type": "bar", "categories": ["Base", "LoRA", "PiSSA", "Ours"],
             "series": [{"name": "acc", "values": [51.0, 58.2, 60.5, 63.9]}],
             "point_roles": ["baseline"] * 3 + ["ours"], "y_min": 40}
    line = {"type": "line", "categories": [str(x) for x in range(100, 700, 100)],
            "series": [{"name": "Lorem", "values": [12, 14, 15, 16, 16.5, 17], "role": "baseline"},
                       {"name": "Ours", "values": [14, 17, 19, 21, 22, 22.5], "role": "ours"}], "y_min": 10}
    C = {
        "title": {"title": "Lorem ipsum dolor sit amet", "subtitle": "Consectetur adipiscing elit",
                  "authors": "Lorem · Ipsum · Dolor"},
        "section": {"number": "02", "title": "Dolor sit amet"},
        "content": {"title": "Lorem ipsum dolor sit amet", "body": L},
        "figure": {"title": "Lorem ipsum dolor", "image": wide, "meaning": "placeholder",
                   "caption": "Figure 1 · lorem ipsum"},
        "figure_text": {"title": "Lorem ipsum dolor", "image": fig, "meaning": "placeholder", "body": L[:3]},
        "comparison": {"title": "Lorem vs. ipsum", "columns": [
            {"head": "Lorem", "items": L[:2]}, {"head": "Ipsum", "items": L[1:3]},
            {"head": "Dolor", "items": L[2:], "role": "ours"}]},
        "statement": {"text": "Lorem ipsum ==dolor sit amet==, consectetur adipiscing elit.", "number": "99.5%",
                      "source": "— Lorem et al., 2026"},
        "formula": {"title": "Lorem ipsum", "latex": ATTENTION,
                    "terms": [("Q,K", "lorem $Q_{i}$ ipsum"), ("V", "dolor sit")]},
        "evidence": {"title": "Lorem ipsum dolor sit amet", "chart": chart,
                     "takeaway": "Lorem ==ipsum== dolor sit amet.", "source": "Lorem et al. (2026), Tab. 1",
                     "rows": [["Method", "A", "B", "C"], ["Base", "1.0", "2.0", "3.0"], ["Lorem", "1.5", "2.2", "3.1"],
                              ["Ours", "1.9", "2.6", "3.4"]], "ours_rows": [3]},
        "diagram": {"title": "Lorem ipsum dolor", "steps": ["Lorem", "Ipsum", {"text": "Dolor", "role": "ours"}, "Sit"],
                    "caption": L[0]},
        "closing": {"message": "Lorem ipsum dolor sit amet.", "takeaways": [L[0], L[1], L[2]],
                    "title": "Thank you", "subtitle": "Questions?", "reference": "Lorem et al., Ipsum, 2026"},
        "credits": {"title": "Credits", "lines": [f"Lorem ipsum {i} · dolor sit amet" for i in range(14)]},
    }
    deck = {"slides": []}
    for kind, vs in templates.LIBRARY.items():
        for v in vs:
            cc = dict(C[kind])
            if v == "evidence_chart_full":
                cc["chart"] = line
            deck["slides"].append({"id": v, "type": kind, "variant": v, "section": "Lorem", "content": cc})
    asm = Assembler(b, vl, m, motif=style_motif(b))
    slides = asm.build(deck)
    issues = check_deck(slides, b)
    p = render_deck(slides, b, vl, m, out / "template_library.pptx")
    pngs = preview.pptx_to_pngs(p, out / "png")
    preview.contact_sheet(pngs, out / "template_library_contact.jpg", cols=5,
                          labels=[f"{i + 1} · {s.meta['variant']}" for i, s in enumerate(slides)],
                          title=f"Template library – {b.label} · {vl.label} · {m.label}")
    cat = {"brand": b.key, "visual_language": vl.key, "motion": m.key, "restraint": st.get("restraint"),
           "word_capacity": templates.catalog(b), "variants": {k: list(v) for k, v in templates.LIBRARY.items()}}
    (out / "catalog.json").write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "check.txt").write_text(report(issues) + "\n\n" + "\n".join(asm.log), encoding="utf-8")
    st["templates"] = {"pptx": str(out / "template_library.pptx"), "slides": len(slides),
                       "errors": sum(i.level == "error" for i in issues)}
    svc.save_state(a.session, st)
    print(out / "template_library_contact.jpg")
    print(report(issues))


def cmd_status(a):
    r = svc.status(a.session)
    for s in r["steps"]:
        print(("✔ " if s["done"] else "· ") + s["label"])
    print("\nNext:", r["next"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("session"); p.add_argument("--paper", required=True)
    p = sub.add_parser("kickoff"); p.add_argument("session"); p.add_argument("--slides", type=int, required=True)
    p.add_argument("--minutes", type=int, default=20); p.add_argument("--notes")
    p.add_argument("--density", choices=list(content.DENSITY), default="medium")
    p.add_argument("--direction", choices=list(svc.DIRECTIONS), default=None)
    p.add_argument("--max-chars", type=int, default=None, help="characters on a plain content slide; other types scale")
    p = sub.add_parser("spec"); p.add_argument("session"); p.add_argument("--file", required=True)
    p.add_argument("--placeholder", action="store_true", help="spec written by the theme agent, not the team")
    p = sub.add_parser("fixes"); p.add_argument("session"); p.add_argument("--target", default="deck")
    p = sub.add_parser("build"); p.add_argument("session"); p.add_argument("--name")
    p.add_argument("--video", action="store_true", help="also the PowerPoint video of the deck (Polish step)")
    p = sub.add_parser("refine"); p.add_argument("session"); p.add_argument("--requests", required=True)
    for c in ("validate", "render"):
        p = sub.add_parser(c); p.add_argument("session"); p.add_argument("stage", choices=svc.STAGES)
    p = sub.add_parser("choose"); p.add_argument("session"); p.add_argument("stage", choices=svc.STAGES)
    p.add_argument("key"); p.add_argument("--patch")
    for c in ("templates", "status"):
        p = sub.add_parser(c); p.add_argument("session")
    a = ap.parse_args()
    fn = {"init": cmd_init, "kickoff": cmd_kickoff, "spec": cmd_spec, "fixes": cmd_fixes, "validate": cmd_validate, "render": cmd_render,
          "choose": cmd_choose, "build": cmd_build, "refine": cmd_refine, "templates": cmd_templates, "status": cmd_status}[a.cmd]
    try:
        r = fn(a)
    except svc.StepError as e:
        sys.exit(f"error: {e}")
    sys.exit(r or 0)


if __name__ == "__main__":
    main()
