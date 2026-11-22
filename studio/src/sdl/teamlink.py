"""Link to the team's content pipeline (GeoRAProject / p2s, LangGraph).

The content side calls three functions (through GeoRAProject/design/render.py):

    guide(state)          -> our slide types, their content slots and the kick-off's character limits,
                             written into the outline / content agents' prompts
    check(deck, run_dir)  -> Feedback dicts: limits, slots, math, images, and the measured fit with the
                             fonts chosen in the Slide Studio (before the human deck review)
    render(deck, run_dir) -> {"pptx_path", "issues"}: the p2s deck becomes our slide spec. Inside a Slide
                             Studio session it is imported into the session (the ★ deck-look step comes
                             next); standalone (CLI / OpenClaw) a deck is rendered with default tokens.

The p2s deck is converted by `to_spec`: same slide ids, our slide types, section dividers inserted at
section changes, the presenter script in the speaker notes, the pipeline slide completed with the
design steps.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import content, service as svc, stages as stg, templates

PROMPT = svc.ROOT / "src" / "agents" / "prompts" / "content_spec_from_paper.md"
# p2s slide types that are ours under another name -> (our type, variant)
TYPE_MAP = {"big_number": ("statement", "statement_number"), "section_divider": ("section", "section_number"),
            "thank_you": ("closing", "closing_thanks"), "workflow": ("diagram", "diagram_flow")}
BODY_TYPES = ["content", "statement", "comparison", "figure", "figure_text", "diagram", "formula", "evidence",
              "closing"]


# ------------------------------------------------------------------ guide
def _types_guide() -> str:
    """'Types and their content keys' + the slot rules, from the content agent's prompt (one source)."""
    md = PROMPT.read_text(encoding="utf-8")
    a = md.index("Types and their content keys")
    b = md.index("Rules:")
    rules = md[b:md.index("Return ONLY")]
    keep = [ln for ln in rules.splitlines() if any(k in ln for k in (
        "zoom", "Builds", "Math in text", "figure with lots of text", "One chart"))]
    return md[a:b].strip() + "\n\nSlot rules:\n" + "\n".join(keep)


def limits_for(state: dict) -> dict:
    sess = (state.get("design") or {}).get("session")
    if sess:
        ko = svc.load_state(sess).get("kickoff") or {}
        if ko.get("char_limits"):
            return ko["char_limits"]
    if state.get("limits"):
        return state["limits"]
    return content.char_limits(state.get("density") or "medium")


def title_limit(session: str | None) -> int | None:
    """Characters of a slide title that fit on one line with the session's heading font."""
    if not session:
        return None
    try:
        return templates.title_chars(stg.compose(svc.token_sections(session)))
    except Exception:
        return None


def guide(state: dict) -> dict:
    lim = dict(limits_for(state))
    tl = title_limit((state.get("design") or {}).get("session"))
    if tl:
        lim["slide_title"] = tl          # every slide's title: one line with the chosen heading font
    return {"types": _types_guide(), "limits": lim, "body_types": BODY_TYPES,
            "type_map": {k: v[0] for k, v in TYPE_MAP.items()},
            "outline_guide": "Slide types: content (title + short lines), statement (one sentence or one big "
                             "number), comparison (2-3 columns), figure / figure_text (a figure from the "
                             "inventory), diagram (3-5 steps), formula (one display formula + up to 3 terms), "
                             "evidence (a bar/line chart or a small table of results), closing (takeaways).",
            "fixed": ["title", "credits", "workflow", "thanks"]}


# ------------------------------------------------------------------ p2s deck -> our spec
def _generic_content(s: dict) -> tuple[dict, str | None]:
    """Fallback for slides written without design slots (generic p2s mode)."""
    t, title, bl = s["type"], s.get("title", ""), s.get("bullets") or []
    figs = s.get("figures") or []
    if t == "title":
        return {"title": title, "subtitle": bl[0] if bl else "", "authors": " · ".join(bl[1:])}, None
    if t in ("figure", "figure_text") and figs:
        f = figs[0]
        c = {"title": title, "image": f["path"], "meaning": f.get("meaning") or f.get("caption", "")}
        return (c | {"caption": f.get("source") or f.get("caption", "")[:90]}, None) if t == "figure" else \
            (c | {"body": bl[:3]}, None)
    if t == "evidence" and s.get("charts"):
        ch = s["charts"][0]                       # p2s marks roles per series; our renderer reads them too
        chart = {"type": "line" if ch.get("kind") == "line" else "bar", "categories": ch["x_labels"],
                 "series": [{"name": x["name"], "values": x["values"], "role": x["role"]} for x in ch["series"]],
                 "fmt": "0.0"}
        return {"title": title, "chart": chart, "takeaway": ch.get("claim", ""),
                "source": ch.get("source") or ""}, "evidence_chart_side"
    if t == "formula" and s.get("formula"):
        return {"title": title, "latex": s["formula"].strip("$"), "terms": []}, None
    if t == "big_number":
        return {"number": s.get("big_number") or "", "number_name": "!!bignum", "text": title}, "statement_number"
    if t == "closing":
        return {"message": title, "takeaways": bl[:3]}, "closing_takeaways"
    if t == "thank_you":
        return {"title": title or "Thank you", "subtitle": "Questions?"}, "closing_thanks"
    if t == "credits":
        return {"title": title or "Credits", "lines": bl}, None
    if t == "workflow":
        return {"title": title, "steps": bl[:5], "caption": ""}, "diagram_flow"
    if t == "comparison":
        half = (len(bl) + 1) // 2
        return {"title": title, "columns": [{"head": "Before", "items": bl[:half]},
                                            {"head": "Ours", "role": "ours", "items": bl[half:]}]}, "comparison_two"
    return {"title": title, "body": bl[:6]}, None


def _design_steps(session: str | None) -> tuple[str, str]:
    """(step label, caption part) describing the design side of this deck."""
    if not session:
        return "Design pipeline", "design: slide-design-lab"
    st = svc.load_state(session)
    ch = st.get("choices") or {}
    n = len(list((svc.sdir(session) / "agents").glob("*.json"))) if (svc.sdir(session) / "agents").exists() else 0
    picked = [ch[k]["label"] for k in ("palette", "type") if k in ch]
    cap = "design: " + (", ".join(picked) if picked else "slide-design-lab") + (f" · {n} design-agent calls" if n else "")
    human = sum(1 for c in ch.values() if c.get("by", "human") == "human")
    if ch and human < len(ch):                         # a headless run: say who chose, never claim a person did
        return (f"Design agents, {len(ch)} auto-picked choices" if not human else
                f"Design agents + {len(ch)} choices ({human} by a person)"), cap
    return f"Design agents + {len(ch) or 6} human choices", cap


def _clamp(text: str, limit: int | None) -> str:
    """Cut at a word boundary to fit `limit` characters (safety net for section titles)."""
    text = str(text or "").strip()
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:–-")
    return cut or text[:limit]


def to_spec(deck: dict, run_dir: str | Path, session: str | None = None) -> dict:
    sec_lim = limits_for({"design": {"session": session}} if session else
                         {"density": (deck.get("design") or {}).get("density")}).get("section")
    sections = {s["id"]: _clamp(s["title"], sec_lim) for s in deck.get("sections") or []}
    out, prev, n_sec, seen = [], None, 0, set()
    fixed_tail = {"credits", "workflow", "thanks"}
    last_sec = None
    for s in deck["slides"]:
        t = s["type"]
        our_t, variant = TYPE_MAP.get(t, (t, None))
        c = dict(s.get("content") or {})
        if not c:
            c, v2 = _generic_content(s)
            variant = variant or v2
        variant = s.get("variant") or variant
        if variant and variant not in templates.LIBRARY.get(our_t, {}):
            variant = None
        sec = sections.get(s.get("section_id"), "") if our_t != "title" else ""
        if s["id"] in fixed_tail and last_sec:
            sec = last_sec
        if our_t != "title" and sec and sec != prev and s["id"] not in fixed_tail and sec not in seen:
            seen.add(sec)
            n_sec += 1
            out.append({"id": f"sec{n_sec}", "type": "section", "section": sec, "variant": "section_number",
                        "content": {"number": f"{n_sec:02d}", "title": sec}})
            prev = sec
        if sec:
            last_sec = sec
        if s["id"] == "workflow" and our_t == "diagram":
            step, cap = _design_steps(session)
            steps = list(c.get("steps") or [])
            if len(steps) >= 4:
                steps[3] = step
                c["steps"] = steps
            c["caption"] = (c.get("caption", "") + " · " + cap).strip(" ·")
        sl = {"id": s["id"], "type": our_t, "section": sec, "content": c,
              "notes": s.get("notes", "")}
        if variant:
            sl["variant"] = variant
        if s.get("zoom"):
            sl["zoom"] = s["zoom"]
        if s.get("seconds"):
            sl["seconds"] = s["seconds"]
        out.append(sl)
    p = deck["paper"]
    spec = {"title": p["title"], "paper": f"{', '.join(p.get('authors') or [])}. {p['title']}. {p.get('venue', '')}",
            "talk_minutes": deck.get("talk_minutes") or 20, "team": deck.get("team") or [],
            "sources": deck.get("sources") or [],
            "_note": f"Written by the team's content pipeline (p2s run {(deck.get('design') or {}).get('run_id', '')}).",
            "slides": out}
    _fit_workflow(spec, session)
    return spec


def _fit_workflow(spec: dict, session: str | None):
    """The pipeline slide's caption grows with the run; keep it inside the diagram limit."""
    lim = limits_for({"design": {"session": session}} if session else {}).get("diagram")
    for s in spec["slides"]:
        if s["id"] == "workflow" and lim:
            while content.chars(s) > lim and s["content"].get("caption"):
                cap = s["content"]["caption"]
                s["content"]["caption"] = cap.rsplit(" · ", 1)[0] if " · " in cap else ""


# ------------------------------------------------------------------ checks -> p2s Feedback
_LINE = re.compile(r"^(?P<id>[^\s:()]+)(?: \((?P<t>[a-z_]+)\))?: (?P<msg>.*)$")
_CHARS = re.compile(r"(?P<n>\d+) characters > limit (?P<lim>\d+)")


def _feedback(msg: str, source: str = "design") -> dict:
    m = _LINE.match(msg)
    sid, text = (m.group("id"), m.group("msg")) if m else ("deck", msg)
    fb = {"slide_id": sid, "issue": "format", "message": text, "source": source,
          "field": None, "limit": None, "actual": None}
    c = _CHARS.search(text)
    if c:
        fb.update(issue="overflow", field="content", limit=int(c.group("lim")), actual=int(c.group("n")))
    elif "$" in text or "math" in text.lower() or "LaTeX" in text:
        fb["issue"] = "math_markup"
    elif "image" in text:
        fb["issue"] = "bad_figure"
    elif "slides (section dividers not counted)" in text:
        fb["slide_id"] = "deck"
    return fb


def fit_issues(spec: dict, session: str) -> list[dict]:
    """Measured fit with the session's chosen fonts and sizes (no rendering, no PowerPoint)."""
    from .assemble import Assembler
    from .brand import Motion
    from .check import check_deck
    t = svc.token_sections(session)
    b, vl = stg.compose(t), stg.visual_of(t)
    slides = Assembler(b, vl, Motion("check", "check", mode="basic")).build(spec)
    from . import fonts
    from .check import _font

    def font_problem(slide, msg: str) -> bool:
        """True when even one short word overflows the box: the font's metrics, not the slide's text."""
        name = msg.split(":", 1)[0]
        el = next((e for e in slide.elements if (e.name or "") == name and e.text), None)
        if el is None:
            return False
        fam, bold = _font(b, el.style)
        pad = el.style.get("pad", 0.0)
        ok, _ = fonts.fits("Word", fam, el.style.get("size", b.sizes["body"]), el.w - 2 * pad,
                           el.h - 2 * el.style.get("vpad", pad), bold, el.style.get("spacing", 1.0) or 1.0)
        return not ok

    out, seen = [], set()
    for i in check_deck(slides, b):
        if i.level != "error" or i.code not in ("overflow", "bounds", "raw-math"):
            continue
        if i.code == "overflow" and font_problem(slides[i.slide - 1], i.msg):
            continue                                       # a design issue (box vs. font), not the content's
        sid = str(slides[i.slide - 1].meta.get("id") or "").split(".")[0]
        name = i.msg.split(":", 1)[0]
        if (sid, i.code, name) in seen or sid.startswith("sec"):
            continue
        seen.add((sid, i.code, name))
        fonts_txt = f"with the chosen fonts ({b.heading_font} / {b.body_font})"
        el = next((e for e in slides[i.slide - 1].elements if (e.name or "") == name and e.text), None)
        if i.code == "overflow" and name == "!!title" and el is not None:
            n = templates.title_chars(b, el.text, el.w)
            out.append({"slide_id": sid, "issue": "overflow", "field": "title", "source": "design",
                        "message": f"the title has {len(el.text)} characters but {fonts_txt} only about {n} fit on "
                                   f"its one line: rewrite the title as a shorter claim (≤ {n} characters)",
                        "limit": n, "actual": len(el.text)})
            continue
        out.append({"slide_id": sid, "issue": "math_markup" if i.code == "raw-math" else "overflow",
                    "message": f"{fonts_txt} the text does not fit: {i.msg}. Shorten this slide's text.",
                    "source": "design", "field": "content", "limit": None, "actual": None})
    return out


def check(deck: dict, run_dir: str) -> list[dict]:
    session = (deck.get("design") or {}).get("session")
    spec = to_spec(deck, run_dir, session)
    base = Path(run_dir)
    if session and "kickoff" in svc.load_state(session):
        res = svc.check_spec(session, spec, base)
        errs = res["errors"]
    else:
        lim = limits_for({"density": (deck.get("design") or {}).get("density")})
        errs, _ = content.validate_spec(spec, lim, base)
    fb = [_feedback(e) for e in errs]
    fb = [f for f in fb if not f["slide_id"].startswith("sec")]
    if not fb and session and "type" in (svc.load_state(session).get("choices") or {}):
        fb += fit_issues(content.resolve(spec, base), session)
    return fb


# ------------------------------------------------------------------ render
def render(deck: dict, run_dir: str) -> dict:
    rd = Path(run_dir)
    session = (deck.get("design") or {}).get("session")
    spec = to_spec(deck, rd, session)
    p = rd / "sdl_spec.json"
    p.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    if session:
        res = svc.import_spec(session, str(p))
        over = [_CHARS.search(e) for e in res.get("errors") or []]
        if not res.get("imported") and over and all(m and int(m["n"]) <= 1.2 * int(m["lim"]) for m in over):
            # only a few characters over the kick-off's guideline: import anyway (the build measures the real
            # fit with the chosen fonts, and the Polish step can shorten a slide)
            res = svc.import_spec(session, str(p), slack=1.2)
            if res.get("imported"):
                res.setdefault("warnings", []).extend(f"imported although: {e}" for e in
                                                      [m.string for m in over])
        if not res.get("imported"):
            fb = [_feedback(e) for e in res["errors"]]
            content_fb = [f for f in fb if not f["slide_id"].startswith(("sec", "deck"))]
            marker = rd / "_design_sent_back"
            if content_fb and not marker.exists():   # once: the content agent can fix these
                marker.write_text("\n".join(res["errors"]), encoding="utf-8")
                return {"pptx_path": None, "issues": content_fb}
            raise RuntimeError("the design side could not import the slides: " + "; ".join(res["errors"])[:1500])
        sd = svc.spec_path(session).parent
        if (rd / "script.md").exists():
            shutil.copy(rd / "script.md", sd / "script.md")
            (sd / "script.html").write_text(script_html((rd / "script.md").read_text(encoding="utf-8")),
                                            encoding="utf-8")
        st = svc.load_state(session)
        st["spec"]["team_run"] = (deck.get("design") or {}).get("run_id")
        st["spec"]["script"] = svc.rel(sd / "script.html") if (sd / "script.html").exists() else None
        svc.save_state(session, st)
        issues = fit_issues(svc.load_spec(session), session) if "type" in st.get("choices", {}) else []
        out = _quick_pptx(svc.load_spec(session), session, sd / "preview.pptx")   # the design choices so far
        st = svc.load_state(session)
        st["spec"]["preview"] = svc.rel(out)
        svc.save_state(session, st)
        return {"pptx_path": str(out), "issues": issues}
    out = _quick_pptx(content.resolve(spec, rd), None, rd / "deck.pptx")
    return {"pptx_path": str(out), "issues": []}


def _quick_pptx(spec: dict, session: str | None, out: Path) -> Path:
    """A plain render (python-pptx, no PowerPoint): the session's tokens so far, basic Morph."""
    from .assemble import Assembler
    from .brand import Motion
    from .render import render_deck
    from .visuals import style_motif
    t = svc.token_sections(session) if session else {}
    b, vl = stg.compose(t), stg.visual_of(t)
    m = Motion("quick", "quick", mode="basic")
    slides = Assembler(b, vl, m, motif=style_motif(b)).build(spec)
    return Path(render_deck(slides, b, vl, m, out))


def script_html(md: str) -> str:
    """The presenter script as a printable page (opened from the Studio)."""
    import html
    out = []
    for ln in md.splitlines():
        e = html.escape(ln)
        if ln.startswith("### "):
            out.append(f"<h3>{e[4:]}</h3>")
        elif ln.startswith("## "):
            out.append(f"<h2>{e[3:]}</h2>")
        elif ln.startswith("# "):
            out.append(f"<h1>{e[2:]}</h1>")
        elif ln.strip():
            out.append(f"<p>{e}</p>")
    css = ("body{font:17px/1.55 system-ui,Segoe UI,sans-serif;max-width:46em;margin:40px auto;padding:0 20px;color:#14161b}"
           "h1{font-size:26px}h2{margin-top:2em;padding-top:.6em;border-top:2px solid #2348d8;color:#2348d8}"
           "h3{font-size:15px;color:#5e6470;margin:1.6em 0 .3em}p{margin:.3em 0}"
           "@media print{body{margin:0;font-size:13pt}h2{break-before:auto}h3{break-after:avoid}}")
    return f"<!doctype html><meta charset='utf-8'><title>Presenter script</title><style>{css}</style>" + "\n".join(out)
