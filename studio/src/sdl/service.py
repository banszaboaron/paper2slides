"""Session service layer: every workflow step as a plain function that returns a dict.

The CLI (tools/workflow.py), the MCP servers (mcp_servers/*) and the web app all call these.
State of one session lives in sessions/<name>/ (session.json is the source of truth).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from . import content, fontfetch, motion as mo, preview, stages as stg
from .assemble import Assembler
from .brand import Brand, VisualLanguage, Motion
from .check import check_deck, report
from .render import render_deck
from .visuals import style_motif

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "sessions"
STAGES = stg.STAGES
STAGE_NEEDS = stg.NEEDS
DIRECTIONS = {
    "academic": "calm, academic – light, restrained, serif or classic sans, lots of air",
    "bold": "bold, expressive – high contrast, strong accent, poster-like typography",
    "tech": "dark, tech – dark ground, vivid accent, geometric or mono type",
    "minimal": "minimal, clean – few colours, lots of white space, fine lines",
}
Log = Callable[[str], None]


class StepError(Exception):
    """A user-facing error: a step was called out of order or with bad input."""


def _noop(_: str):
    pass


# ------------------------------------------------------------------ state
def sdir(name: str) -> Path:
    if not name or any(c in name for c in "/\\.:"):
        raise StepError(f"invalid session name '{name}'")
    return SESSIONS / name


def load_state(name: str) -> dict:
    p = sdir(name) / "session.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"name": name, "choices": {}}


def save_state(name: str, st: dict):
    p = sdir(name) / "session.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


def token_sections(name: str) -> dict:
    return load_state(name).get("tokens", {})


def tokens(name: str) -> tuple[Brand, VisualLanguage, Motion]:
    t = token_sections(name)
    from . import edits
    return edits.patch_brand(stg.compose(t), _overrides(name).get("_brand")), stg.visual_of(t), stg.motion_of(t)


def _overrides(name: str) -> dict:
    p = sdir(name) / "spec" / "overrides.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def rel(p: Path | str) -> str:
    """Path relative to the lab root (what the web app serves under /files/)."""
    return Path(p).resolve().relative_to(ROOT).as_posix()


def list_sessions() -> list[dict]:
    out = []
    if SESSIONS.exists():
        for d in sorted(SESSIONS.iterdir()):
            if (d / "session.json").exists():
                st = load_state(d.name)
                out.append({"name": d.name, "title": (st.get("paper") or {}).get("title_guess", ""),
                             "next": status(d.name)["next"]})
    return out


# ------------------------------------------------------------------ paper, brief, kick-off
def ingest_paper(name: str, pdf: str) -> dict:
    import pymupdf
    d = sdir(name)
    (d / "paper").mkdir(parents=True, exist_ok=True)
    (d / "options").mkdir(exist_ok=True)
    src = Path(pdf)
    if not src.exists():
        raise StepError(f"PDF not found: {pdf}")
    dst = d / "paper" / src.name
    if src.resolve() != dst.resolve():
        shutil.copy(src, dst)
    doc = pymupdf.open(str(dst))
    text = "\n\n".join(f"[page {i + 1}]\n{p.get_text()}" for i, p in enumerate(doc))
    (d / "paper" / "text.txt").write_text(text, encoding="utf-8")
    lines = [x.strip() for x in text.split("\n") if x.strip() and not x.startswith("[page")]
    meta = {"file": dst.name, "pages": len(doc), "chars": len(text), "scanned": is_scanned(doc),
            "embedded_images": sum(len(p.get_images()) for p in doc),
            "title_guess": (doc.metadata or {}).get("title") or " ".join(lines[:2])[:160]}
    try:                                              # the paper's own figures, for the content agent
        from . import figures
        figs = figures.extract(dst, d / "paper" / "figures")
    except Exception as e:  # noqa: BLE001 - a paper without usable figures is still a paper
        figs = []
        meta["figure_error"] = str(e)[:200]
    meta["figures"] = len(figs)
    st = load_state(name)
    st["paper"] = meta
    srcs = [s for s in st.get("sources") or [] if s.get("role") != "paper"]
    st["sources"] = [{"file": f"paper/{dst.name}", "role": "paper", "comment": "", "title": meta["title_guess"],
                      "pages": meta["pages"], "chars": meta["chars"], "scanned": meta["scanned"]}] + srcs
    save_state(name, st)
    return {"paper": meta, "text": rel(d / "paper" / "text.txt")}


# ------------------------------------------------------------------ more documents, team
SOURCE_ROLES = ("paper", "own_results", "notes", "other")


def is_scanned(doc) -> bool:
    """A PDF whose pages are pictures (slides exported as images, scans): almost no text layer."""
    n = sum(len(p.get_text().strip()) for p in doc)
    return n < 120 * max(1, len(doc))


def source_text_path(name: str, index: int) -> Path:
    s = sources(name)[index]
    return sdir(name) / "paper" / "text.txt" if s["role"] == "paper" else (sdir(name) / s["file"]).with_suffix(".txt")


def save_read_text(name: str, index: int, text: str) -> dict:
    """The text Claude read off a picture-only PDF replaces the empty text layer."""
    source_text_path(name, index).write_text(text, encoding="utf-8")
    st = load_state(name)
    srcs = sources(name)
    srcs[index].update(chars=len(text), read_by="claude")
    st["sources"] = srcs
    if index == 0 and st.get("paper"):
        st["paper"].update(chars=len(text), read_by="claude")
    save_state(name, st)
    return srcs[index]


def sources(name: str) -> list[dict]:
    st = load_state(name)
    out = list(st.get("sources") or [])
    if not out and st.get("paper"):                    # sessions from before multi-document support
        pdf = next(iter((sdir(name) / "paper").glob("*.pdf")), None)
        if pdf:
            out = [{"file": f"paper/{pdf.name}", "role": "paper", "comment": "",
                    "title": st["paper"].get("title_guess", ""), "pages": st["paper"].get("pages")}]
    for s in out:                                      # sessions from before the picture-PDF check
        if "scanned" not in s and (sdir(name) / s["file"]).exists():
            import pymupdf
            with pymupdf.open(str(sdir(name) / s["file"])) as doc:
                s["scanned"] = is_scanned(doc)
    return out


def add_source(name: str, pdf: str, role: str = "own_results", comment: str = "") -> dict:
    """Another document next to the paper (e.g. the team's replication report), with how to use it."""
    import pymupdf
    if role not in SOURCE_ROLES[1:]:
        raise StepError(f"role must be one of {list(SOURCE_ROLES[1:])}")
    src = Path(pdf)
    if not src.exists() or src.suffix.lower() != ".pdf":
        raise StepError(f"PDF not found: {pdf}")
    st = load_state(name)
    if "paper" not in st:
        raise StepError("add the paper first")
    d = sdir(name) / "paper" / "extra"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / src.name
    if src.resolve() != dst.resolve():
        shutil.copy(src, dst)
    doc = pymupdf.open(str(dst))
    text = "\n\n".join(f"[page {i + 1}]\n{p.get_text()}" for i, p in enumerate(doc))
    dst.with_suffix(".txt").write_text(text, encoding="utf-8")
    lines = [x.strip() for x in text.split("\n") if x.strip() and not x.startswith("[page")]
    title = (doc.metadata or {}).get("title") or " ".join(lines[:2])[:160]
    srcs = sources(name)
    srcs = [s for s in srcs if s["file"] != f"paper/extra/{dst.name}"]
    srcs.append({"file": f"paper/extra/{dst.name}", "role": role, "comment": comment, "title": title,
                 "pages": len(doc), "chars": len(text), "scanned": is_scanned(doc)})
    st["sources"] = srcs
    save_state(name, st)
    return {"sources": srcs}


def update_source(name: str, index: int, comment: str | None = None, role: str | None = None) -> dict:
    st = load_state(name)
    srcs = sources(name)
    if not 0 <= index < len(srcs):
        raise StepError(f"no document #{index}")
    if comment is not None:
        srcs[index]["comment"] = comment
    if role is not None and index > 0:
        if role not in SOURCE_ROLES[1:]:
            raise StepError(f"role must be one of {list(SOURCE_ROLES[1:])}")
        srcs[index]["role"] = role
    st["sources"] = srcs
    save_state(name, st)
    return {"sources": srcs}


def remove_source(name: str, index: int) -> dict:
    st = load_state(name)
    srcs = sources(name)
    if index <= 0 or index >= len(srcs):
        raise StepError("the paper itself cannot be removed (start a new session instead)")
    srcs.pop(index)
    st["sources"] = srcs
    save_state(name, st)
    return {"sources": srcs}


CLOSINGS = [("Thank you", "Questions?"), ("Thank you for your attention", ""),
            ("Thank you for your attention", "Questions?"), ("Questions?", "Thank you for your attention")]


def set_team(name: str, team: list[str], venue: str = "", closing_title: str = "Thank you",
             closing_subtitle: str = "Questions?") -> dict:
    """Presenters (title slide), venue line, and the last slide's words. Who presents which slide is
    decided by the team, not by the pipeline."""
    team = [t.strip() for t in team if t and t.strip()]
    if not team:
        raise StepError("give at least one presenter's name")
    if not closing_title.strip():
        raise StepError("the last slide needs a line, e.g. 'Thank you for your attention'")
    st = load_state(name)
    st["team"] = {"members": team, "venue": venue.strip(),
                  "closing": {"title": closing_title.strip()[:60], "subtitle": closing_subtitle.strip()[:60]}}
    save_state(name, st)
    return st["team"]


LLM_PROVIDERS = ("anthropic", "claude_cli")
LLM_MODELS = ("sonnet", "opus", "haiku")


def set_llm(name: str, provider: str, model: str = "sonnet") -> dict:
    """The model behind every agent of this session: design agents, visual judge and the team's pipeline."""
    if provider not in LLM_PROVIDERS:
        raise StepError(f"provider must be one of {list(LLM_PROVIDERS)}")
    if model not in LLM_MODELS:
        raise StepError(f"model must be one of {list(LLM_MODELS)}")
    st = load_state(name)
    st["llm"] = {"provider": provider, "model": model}
    save_state(name, st)
    return st["llm"]


def sources_text(name: str, budget: int = 120000) -> str:
    """Every document's text with its role and comment (for the agents); the budget is shared."""
    srcs = sources(name)
    texts = []
    for s in srcs:
        p = sdir(name) / "paper" / "text.txt" if s["role"] == "paper" else (sdir(name) / s["file"]).with_suffix(".txt")
        texts.append(p.read_text(encoding="utf-8") if p.exists() else "")
    if len(srcs) <= 1:
        return texts[0][:budget] if texts else paper_text(name)[:budget]
    share = budget // len(srcs)
    out = []
    for k, (s, t) in enumerate(zip(srcs, texts), 1):
        head = f"=== DOCUMENT {k}: {s.get('title', '')} (role: {s['role']})"
        if s.get("comment"):
            head += f" | how to use it: {s['comment']}"
        out.append(head + " ===\n" + t[:share])
    return "\n\n".join(out)


def paper_figures(name: str) -> list[dict]:
    p = sdir(name) / "paper" / "figures" / "figures.json"
    if not p.exists():
        pdfs = list((sdir(name) / "paper").glob("*.pdf"))
        if not pdfs:
            return []
        from . import figures
        return figures.extract(pdfs[0], sdir(name) / "paper" / "figures")
    return json.loads(p.read_text(encoding="utf-8"))


def paper_text(name: str) -> str:
    p = sdir(name) / "paper" / "text.txt"
    if not p.exists():
        raise StepError("ingest the paper first")
    return p.read_text(encoding="utf-8")


def get_brief(name: str) -> dict | None:
    p = sdir(name) / "brief.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_brief(name: str, brief: dict) -> dict:
    for k in ("title", "one_line", "mood", "communication_needs"):
        if k not in brief:
            raise StepError(f"brief is missing '{k}'")
    (sdir(name) / "brief.json").write_text(json.dumps(brief, indent=1, ensure_ascii=False), encoding="utf-8")
    return brief


def kickoff(name: str, slides: int, minutes: int = 20, density: str = "medium",
            max_chars: int | None = None, notes: str = "", direction: str = "") -> dict:
    if density not in content.DENSITY:
        raise StepError(f"density must be one of {list(content.DENSITY)}")
    if direction and direction not in DIRECTIONS:
        raise StepError(f"direction must be one of {list(DIRECTIONS)}")
    st = load_state(name)
    lim = content.char_limits(density, max_chars)
    st["kickoff"] = {"logical_slides": slides, "talk_minutes": minutes, "notes": notes or "",
                     "density": density, "max_chars": max_chars, "char_limits": lim, "direction": direction,
                     "direction_text": DIRECTIONS.get(direction, "")}
    save_state(name, st)
    (sdir(name) / "limits.json").write_text(json.dumps(
        {"logical_slides": slides, "talk_minutes": minutes, "max_visible_chars_per_slide": lim,
         "note": "visible characters incl. spaces; chart labels, LaTeX, sources and speaker notes do not count"},
        indent=1, ensure_ascii=False), encoding="utf-8")
    return {"kickoff": st["kickoff"], "limits_file": rel(sdir(name) / "limits.json")}


# ------------------------------------------------------------------ slide spec
def spec_path(name: str) -> Path:
    return sdir(name) / "spec" / "deck.json"


def load_spec(name: str) -> dict:
    p = spec_path(name)
    if not p.exists():
        raise StepError("no slide spec yet: import one first")
    deck = content.resolve(json.loads(p.read_text(encoding="utf-8")), p.parent)
    ov = p.parent / "overrides.json"                 # layout fixes accepted from the visual judge
    if ov.exists():
        return with_overrides(deck, json.loads(ov.read_text(encoding="utf-8")))
    return deck


def with_overrides(deck: dict, fixes: dict) -> dict:
    """Variants, text replacements and the Polish step's design edits, applied to a resolved spec."""
    for sl in deck["slides"]:
        fx = fixes.get(sl["id"])
        if isinstance(fx, str):                  # older format: just a variant
            fx = {"variant": fx}
        if not isinstance(fx, dict):
            continue
        if fx.get("variant"):
            sl["variant"] = fx["variant"]
        for find, repl in fx.get("text", []):
            sl["content"] = _replace_text(sl.get("content") or {}, find, repl)
        if fx.get("edit"):
            sl["_edit"] = fx["edit"]
    return deck


def _replace_text(x, find: str, repl: str):
    if isinstance(x, str):
        return x.replace(find, repl)
    if isinstance(x, dict):
        return {k: (v if k in ("image", "latex") else _replace_text(v, find, repl)) for k, v in x.items()}
    if isinstance(x, list):
        return [_replace_text(v, find, repl) for v in x]
    return x


def save_judge(name: str, target: str, result: dict) -> dict:
    st = load_state(name)
    st.setdefault("judge", {})[target] = result
    save_state(name, st)
    return result


def judge_inputs(name: str, target: str) -> tuple[list[str], list[str], str]:
    """(pngs, slide ids, label) of the final deck ('deck') or of one motion option ('motion:<key>')."""
    st = load_state(name)
    if target == "deck":
        d = st.get("deck") or {}
        if not d.get("pngs"):
            raise StepError("build the deck first")
        return d["pngs"], d.get("ids") or [str(i + 1) for i in range(len(d["pngs"]))], "final deck"
    if target.startswith("motion:"):
        key = target.split(":", 1)[1]
        rend = (st.get("renders") or {}).get("motion", {})
        row = next((r for r in rend.get("options", []) if r["key"] == key), None)
        if not row:
            raise StepError(f"no rendered motion option '{key}'")
        return row["pngs"], row.get("ids") or [], row.get("label", key)
    raise StepError(f"unknown judge target {target}")


def apply_fixes(name: str, target: str = "deck") -> dict:
    """Accept the judge's layout-variant fixes (they go to spec/overrides.json; the spec itself is untouched)."""
    from . import templates
    st = load_state(name)
    j = (st.get("judge") or {}).get(target)
    if not j:
        raise StepError("run the review first")
    _, ids, _ = judge_inputs(name, target)
    spec = json.loads(spec_path(name).read_text(encoding="utf-8"))
    types = {sl["id"]: sl["type"] for sl in spec["slides"]}
    ov_path = spec_path(name).parent / "overrides.json"
    ov = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    applied, rejected = [], []
    texts = {sl["id"]: json.dumps(sl.get("content") or {}, ensure_ascii=False) for sl in spec["slides"]}
    for it in j.get("slides", []):
        fx = it.get("fix") or {}
        n = it.get("slide")
        if not isinstance(n, int) or not 1 <= n <= len(ids):
            continue
        sid = ids[n - 1].split(".")[0]
        cur = ov.get(sid)
        cur = {"variant": cur} if isinstance(cur, str) else dict(cur or {})
        if fx.get("type") == "variant" and fx.get("variant") in templates.LIBRARY.get(types.get(sid, ""), {}):
            lost = _variant_loses_text(name, sid, fx["variant"], cur)
            if lost:                                  # a layout fix must never delete content
                rejected.append({"slide": n, "id": sid, "variant": fx["variant"],
                                 "reason": "the new layout would drop: " + ", ".join(lost[:6])})
                continue
            cur["variant"] = fx["variant"]
            applied.append({"slide": n, "id": sid, "variant": fx["variant"]})
        elif fx.get("type") == "text" and fx.get("find") and isinstance(fx.get("replace"), str) \
                and json.dumps(fx["find"], ensure_ascii=False)[1:-1] in texts.get(sid, ""):
            cur.setdefault("text", []).append([fx["find"], fx["replace"]])
            applied.append({"slide": n, "id": sid, "text": fx["replace"][:60]})
        else:
            continue
        ov[sid] = cur
    ov_path.write_text(json.dumps(ov, indent=1), encoding="utf-8")
    return {"applied": applied, "rejected": rejected, "overrides": ov}


def _built_words(name: str, sid: str, ov_entry: dict) -> set[str]:
    """The words that end up on the physical slides of one logical slide, laid out with the session's tokens."""
    import re as _re
    deck = load_spec(name)
    one = [sl for sl in deck["slides"] if sl["id"] == sid]
    if not one:
        return set()
    sl = dict(one[0])
    if ov_entry.get("variant"):
        sl["variant"] = ov_entry["variant"]
    b, vl, m = tokens(name)
    words = set()
    for s in Assembler(b, vl, m, motif=style_motif(b)).build({**deck, "slides": [sl]}):
        for e in s.elements:
            if e.role == "content" and e.kind in ("text", "chart", "formula"):
                words |= {w.lower() for w in _re.findall(r"[A-Za-z0-9.%]{2,}", f"{e.text} {e.data or ''}")}
    return words


def _variant_loses_text(name: str, sid: str, variant: str, cur: dict) -> list[str]:
    """Words shown with the current layout that the proposed layout would no longer show."""
    try:
        before = _built_words(name, sid, cur)
        after = _built_words(name, sid, {**cur, "variant": variant})
    except Exception:                                  # cannot verify -> do not risk it
        return ["(could not lay the slide out with the new variant)"]
    return sorted(before - after)


def check_spec(name: str, deck: dict, base: Path, slack: float = 1.0) -> dict:
    st = load_state(name)
    if "kickoff" not in st:
        raise StepError("run the kick-off first (it sets the character limits)")
    lim = {k: int(v * slack) for k, v in st["kickoff"]["char_limits"].items()}
    errs, warns = content.validate_spec(deck, lim, base)
    if not errs:                                      # every slide must be buildable by its template
        from .brand import Motion as _M
        asm = Assembler(stg.DEFAULT, stg.visual_of({}), _M("check", "check"))
        for ls in content.resolve(deck, base).get("slides", []):
            try:
                asm.choose(ls, None)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{ls.get('id')} ({ls.get('type')}): the content does not fit the slide type's keys "
                            f"({type(e).__name__}: {e})")
    want = st["kickoff"].get("logical_slides")
    have = sum(1 for s in deck.get("slides") or [] if s.get("type") != "section")
    if want and abs(have - want) > 2:                # the kick-off count is a contract with the content team
        errs.append(f"the spec has {have} slides (section dividers not counted), the kick-off asked for {want} (±2). "
                    f"Change the content, or set {have} slides at the kick-off.")
    return {"ok": not errs, "errors": errs, "warnings": warns}


def import_spec(name: str, file: str, placeholder: bool = False, slack: float = 1.0) -> dict:
    src = Path(file)
    if not src.exists():
        raise StepError(f"spec not found: {file}")
    d = spec_path(name).parent
    deck = content.import_spec(src, d)
    res = check_spec(name, deck, d, slack)
    if not res["ok"]:
        return res | {"imported": False}
    spec_path(name).write_text(json.dumps(deck, indent=1, ensure_ascii=False), encoding="utf-8")
    (d / "overrides.json").unlink(missing_ok=True)     # review fixes belonged to the previous content
    st = load_state(name)
    st.get("renders", {}).pop("motion", None)           # the deck previews showed the previous content
    (st.get("judge") or {}).clear() if isinstance(st.get("judge"), dict) else None
    kinds: dict[str, int] = {}
    for s in deck["slides"]:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    st["spec"] = {"file": rel(spec_path(name)), "slides": len(deck["slides"]), "placeholder": bool(placeholder),
                  "source": src.name, "kinds": kinds}
    st.pop("deck", None)
    st.pop("refine", None)
    shutil.rmtree(d / "history", ignore_errors=True)
    save_state(name, st)
    return res | {"imported": True, "slides": len(deck["slides"]), "kinds": kinds, "placeholder": bool(placeholder),
                  "choices_kept": list(st["choices"])}


# ------------------------------------------------------------------ ★ stages
def stage_base(name: str, stage: str) -> dict:
    if stage not in STAGES:
        raise StepError(f"unknown stage {stage}")
    st = load_state(name)
    if "kickoff" not in st:
        raise StepError("answer the kick-off questions first")
    need = STAGE_NEEDS.get(stage)
    if need and need not in st["choices"]:
        raise StepError(f"choose a '{stg.LABELS_HU[need]}' option first")
    if stage == "motion" and "spec" not in st:
        raise StepError("import the slide spec first: the motion previews are the real deck")
    return {"tokens": st.get("tokens", {})}


def options_path(name: str, stage: str) -> Path:
    return sdir(name) / "options" / f"{stage}.json"


def get_options(name: str, stage: str) -> dict | None:
    p = options_path(name, stage)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def validate_options(name: str, stage: str, data: dict | None = None) -> dict:
    data = data if data is not None else get_options(name, stage)
    if data is None:
        raise StepError(f"no options/{stage}.json yet")
    errs, warns = stg.validate(stage, data, stage_base(name, stage)["tokens"])
    return {"ok": not errs, "errors": errs, "warnings": warns}


def save_options(name: str, stage: str, data: dict) -> dict:
    data = dict(data, stage=stage)
    res = validate_options(name, stage, data)
    p = options_path(name, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    st = load_state(name)
    st.get("renders", {}).pop(stage, None)
    save_state(name, st)
    return res | {"file": rel(p)}


def _issue_dicts(issues) -> list[dict]:
    return [{"slide": i.slide, "level": i.level, "code": i.code, "msg": i.msg} for i in issues]


def build_preview(out_dir: Path, brand, vl, mot, deck: dict, gif: bool = False, video: bool = False,
                  log: Log = _noop, pipeline_video: bool = False):
    out_dir.mkdir(parents=True, exist_ok=True)
    asm = Assembler(brand, vl, mot, motif=style_motif(brand))
    asm.with_video = pipeline_video
    slides = asm.build(deck)
    issues = check_deck(slides, brand)
    pptx = render_deck(slides, brand, vl, mot, out_dir / "preview.pptx")
    ppt = preview.backend() == "powerpoint"
    # real 3D models via COM hang PowerPoint's import dialog on some machines: opt-in only
    if ppt and os.environ.get("SDL_3D_MODELS") == "1" and Path(str(pptx) + ".world3d.json").exists():
        from . import office
        try:
            n = office.add_3d_models(str(pptx))
            log(f"{out_dir.name}: {n} 3D models placed")
        except Exception as e:                       # keep the picture fallback
            log(f"{out_dir.name}: 3D model not placed ({e}); picture fallback kept")
    log(f"{out_dir.name}: {len(slides)} slides -> PNG")
    pngs = preview.pptx_to_pngs(pptx, out_dir / "png")
    labels = [f"{i + 1} · {s.meta.get('id')} · {s.meta.get('variant')}" + (" · MORPH" if s.transition == "morph" else "")
              for i, s in enumerate(slides)]
    (out_dir / "check.txt").write_text(report(issues) + "\n\n" + "\n".join(asm.log), encoding="utf-8")
    sheet = preview.contact_sheet(pngs, out_dir / "contact.jpg", cols=min(5 if len(pngs) > 12 else 4, len(pngs)),
                                  labels=labels)
    gif_path = video_path = None
    if video and ppt:
        from . import office
        log(f"{out_dir.name}: PowerPoint video")
        try:
            rep = office.export(str(pptx), png=False, pdf=False, mp4=True, seconds_per_slide=2, height=720)
            mp4 = [f for f in rep["files"] if f.endswith(".mp4")]
            if mp4:
                video_path = out_dir / "preview.mp4"
                Path(mp4[0]).replace(video_path)
        except Exception as e:
            log(f"{out_dir.name}: video failed ({e})")
    if (gif or (video and not video_path)) and mot.mode != "none":
        pairs = [(i - 1, i) for i in range(1, len(slides)) if slides[i].transition == "morph"]
        frames, durs = [], []
        for p in pairs:
            frames += mo.morph_frames(slides, [p], steps=(0, .25, .5, .75, 1))
            durs += [600] + [80] * 3 + [600]
        if frames:
            log(f"{out_dir.name}: Morph simulation ({len(frames)} frames)")
            fp = render_deck(frames, brand, vl, mot, out_dir / "_frames.pptx")
            fpng = preview.pptx_to_pngs(fp, out_dir / "_frames", width=960)
            gif_path = preview.gif(fpng, out_dir / "morph_sim.gif", ms=durs)
            fp.unlink()
    return {"sheet": sheet, "issues": issues, "pngs": pngs, "pptx": pptx, "gif": gif_path, "video": video_path,
            "slides": slides, "log": asm.log}


def _font_files(b: Brand) -> dict:
    from . import fonts
    table = {**fonts.FAMILY_FILES, **fonts._registry_files()}
    out = {}
    for f in {b.heading_font, b.body_font, b.mono_font}:
        reg, bold = table.get((f, False)), table.get((f, True))
        if reg:
            out[f] = {"regular": "fonts/" + reg, "bold": "fonts/" + (bold or reg)}
    return out


def render_options(name: str, stage: str, log: Log = _noop) -> dict:
    res = validate_options(name, stage)
    if not res["ok"]:
        raise StepError("options do not validate: " + "; ".join(res["errors"]))
    fontfetch.install_for_preview()
    data = get_options(name, stage)
    base = stage_base(name, stage)["tokens"]
    out = sdir(name) / "options" / stage
    result = {"stage": stage, "kind": "board", "options": []}
    deck = None
    if stage == "layout":
        from .fixtures import preview_deck
        result["kind"] = "slides"
    elif stage == "motion":
        deck = load_spec(name)
        result["kind"] = "deck"
        result["slides"] = len(deck["slides"])
    for k, o in enumerate(data["options"], 1):
        t = stg.with_option(base, stage, o)
        b, vl, m = stg.compose(t), stg.visual_of(t), stg.motion_of(t)
        row = {"key": o["key"], "label": o.get("label", o["key"]), "rationale": o.get("rationale", ""),
               "section": o.get(stg.SECTION[stage]), "resolved": {"brand": b.to_dict(), "visual_language": vl.__dict__ | {},
                                                                   "motion": m.__dict__ | {}, "fonts": _font_files(b)}}
        if stage in ("layout", "motion"):
            log(f"option {k}/4: {o.get('label', o['key'])}")
            if stage == "layout":
                d = preview_deck(b, ROOT / "outputs" / "_cache", full=True)
                mm = Motion("preview", "preview", mode="basic")
                r = build_preview(out / o["key"], b, vl, mm, d, log=log)
            else:
                r = build_preview(out / o["key"], b, vl, m, deck, video=True, log=log)
            issues = _issue_dicts(r["issues"])
            row.update({"contact": rel(r["sheet"]), "pngs": [rel(x) for x in r["pngs"]],
                        "ids": [str(sl.meta.get("id")) for sl in r["slides"]],
                        "gif": rel(r["gif"]) if r["gif"] else None, "video": rel(r["video"]) if r["video"] else None,
                        "pptx": rel(r["pptx"]), "errors": sum(i["level"] == "error" for i in issues),
                        "fit": [i for i in issues if i["code"] in ("overflow", "bounds")]})
        result["options"].append(row)
    st = load_state(name)
    st.setdefault("renders", {})[stage] = result
    if stage == "motion":                            # reviews of the previous motion decks are stale
        st["judge"] = {k: v for k, v in (st.get("judge") or {}).items() if not k.startswith("motion:")}
    save_state(name, st)
    return result


def choose(name: str, stage: str, key: str, patch: dict | None = None, by: str = "human") -> dict:
    st = load_state(name)
    data = get_options(name, stage)
    if data is None:
        raise StepError(f"no options/{stage}.json")
    o = next((x for x in data["options"] if x["key"] == key), None)
    if o is None:
        raise StepError(f"no option '{key}' in options/{stage}.json")
    o = json.loads(json.dumps(o))
    if patch:
        o.setdefault(stg.SECTION[stage], {}).update(patch)
    stage_base(name, stage)
    t = stg.with_option(st.get("tokens", {}), stage, o)
    b = stg.compose(t)
    if stage in ("palette", "type"):
        from . import options as opt_mod
        errs = opt_mod.ensure_fonts(b) + opt_mod.check_brand(b)[0]
        if errs:
            raise StepError("chosen option is not usable: " + "; ".join(errs))
    st["tokens"] = t
    st["tokens"]["brand"] = b.to_dict()
    st["choices"][stage] = {"key": key, "label": o.get("label", key), "patch": patch or {},
                            "rationale": o.get("rationale", ""), "by": by}
    later = STAGES[STAGES.index(stage) + 1:]
    for s2 in later:                                  # later previews were drawn with the old choice
        st.get("renders", {}).pop(s2, None)
    if stage == "motion":
        rend = (st.get("renders") or {}).get("motion", {})
        row = next((r for r in rend.get("options", []) if r["key"] == key), None)
        st["deck_from_motion"] = row.get("pptx") if row else None
    st.pop("deck", None)                              # the final deck must be rebuilt with every new choice
    st.pop("refine", None)                            # ...and polished again
    (st.get("judge") or {}).pop("deck", None)
    save_state(name, st)
    (sdir(name) / "tokens.json").write_text(json.dumps(st["tokens"], indent=1, ensure_ascii=False), encoding="utf-8")
    return {"stage": stage, "chosen": key, "patch": patch or {}, "stale": later}


# ------------------------------------------------------------------ deck
def build(name: str, deck_name: str | None = None, log: Log = _noop, video: bool = False) -> dict:
    st = load_state(name)
    if "motion" not in st["choices"]:
        raise StepError("finish the ★ stages first")
    fontfetch.install_for_preview()
    b, vl, m = tokens(name)
    deck = load_spec(name)
    out = sdir(name) / "deck"
    if out.exists():
        shutil.rmtree(out / "png", ignore_errors=True)
    (out / "preview.mp4").unlink(missing_ok=True)
    r = build_preview(out, b, vl, m, deck, gif=False, video=video, log=log, pipeline_video=True)
    nm = deck_name or name
    files = {"pptx": out / f"{nm}.pptx", "contact": out / f"{nm}_contact.jpg", "check": out / f"{nm}_check.txt"}
    if r.get("video"):
        files["video"] = out / f"{nm}.mp4"
        Path(r["video"]).replace(files["video"])
    elif r.get("gif"):
        files["gif"] = Path(r["gif"])
    (out / "preview.pptx").replace(files["pptx"])
    side = out / "preview.pptx.world3d.json"
    if side.exists():
        side.replace(Path(str(files["pptx"]) + ".world3d.json"))
    (out / "contact.jpg").replace(files["contact"])
    (out / "check.txt").replace(files["check"])
    from .check import font_audit                    # the chosen fonts really are the deck's fonts
    fa = font_audit(files["pptx"], b)
    if fa:
        with files["check"].open("a", encoding="utf-8") as f:
            f.write("\n\nfont audit:\n" + "\n".join(map(str, fa)))
    issues = _issue_dicts(r["issues"] + fa)
    st["deck"] = {k: rel(v) for k, v in files.items()} | {
        "errors": sum(i["level"] == "error" for i in issues), "slides": len(r["slides"]),
        "pngs": [rel(p) for p in r["pngs"]], "ids": [str(sl.meta.get("id")) for sl in r["slides"]],
        "placeholder_spec": st.get("spec", {}).get("placeholder", False)}
    st.get("judge", {}).pop("deck", None)
    rf = st.get("refine") or {}
    if rf.get("prev") is not None and rf.get("history"):
        h = rf["history"][-1]
        h.update(_compare(rf.pop("prev"), st["deck"]))
        asked = list(dict.fromkeys(str(r["id"]).split(".")[0] for r in h.get("requests") or []))
        h["asked_same"] = [x for x in asked if x not in h["changed"] and x not in h["removed"]]
    save_state(name, st)
    return st["deck"] | {"issues": issues}


def _compare(prev: dict, deck: dict) -> dict:
    """Which slides look different from the previous version (pixel comparison, by slide id)."""
    from PIL import Image, ImageChops, ImageStat
    def small(p):
        return Image.open(ROOT / p).convert("L").resize((320, 180))
    now: dict[str, list[str]] = {}
    for sid, p in zip(deck.get("ids") or [], deck["pngs"]):
        now.setdefault(sid, []).append(p)
    changed, same = [], []
    for sid, ps in now.items():
        old = prev.get(sid)
        if not old or len(old) != len(ps):
            changed.append(sid)
            continue
        diff = max(ImageStat.Stat(ImageChops.difference(small(a), small(b))).mean[0] for a, b in zip(old, ps))
        (same if diff < 0.6 else changed).append(sid)
    root = lambda x: x.split(".")[0]                   # zoom sub-slides belong to their slide
    order = list(dict.fromkeys(root(k) for k in now))
    ch = {root(x) for x in changed}
    return {"changed": [x for x in order if x in ch], "same": len(set(order) - ch),
            "removed": sorted({root(k) for k in prev} - set(order))}


# ------------------------------------------------------------------ polish (final touches before the export)
def _refine(st: dict) -> dict:
    return st.setdefault("refine", {"round": 0, "history": [], "approved": False})


def _hist_dir(name: str, rnd: int) -> Path:
    return spec_path(name).parent / "history" / f"r{rnd}"


def refine_snapshot(name: str) -> int:
    """Keep the current spec + overrides as version `round` (so a later round can be undone)."""
    st = load_state(name)
    rf = _refine(st)
    d = _hist_dir(name, rf["round"])
    d.mkdir(parents=True, exist_ok=True)
    for f in ("deck.json", "overrides.json"):
        p = spec_path(name).parent / f
        if p.exists():
            shutil.copy2(p, d / f)
        else:
            (d / f).unlink(missing_ok=True)
    save_state(name, st)
    return rf["round"]


def refine_apply(spec: dict, changes: list[dict], ov: dict) -> tuple[dict, dict, list[str]]:
    """Apply the agent's changes to a raw spec (copy). -> (spec, overrides, errors)."""
    spec, ov = json.loads(json.dumps(spec)), json.loads(json.dumps(ov))
    errs = []
    slides = spec["slides"]
    idx = lambda i: next((k for k, s in enumerate(slides) if s["id"] == i), None)
    fixed = {"title", "credits"}
    for c in changes:
        op, sid = c.get("op", "update"), str(c.get("id") or "")
        if op == "update":
            k = idx(sid)
            if k is None:
                errs.append(f"update: no slide with id '{sid}'")
                continue
            s = slides[k]
            if isinstance(c.get("content"), dict):
                old = s.get("content") or {}
                new = dict(c["content"])
                for key in ("image", "image_name", "number_name"):     # files and names are not the agent's to change
                    if key in old and key not in new:
                        new[key] = old[key]
                s["content"] = new
                cur = ov.get(sid)
                if isinstance(cur, dict):
                    cur.pop("text", None)
            if c.get("variant"):
                s["variant"] = c["variant"]
                if isinstance(ov.get(sid), dict):
                    ov[sid].pop("variant", None)
                elif isinstance(ov.get(sid), str):
                    ov.pop(sid)
            if isinstance(c.get("notes"), str):
                s["notes"] = c["notes"]
            if sid in ov and not ov[sid]:
                ov.pop(sid)
        elif op == "delete":
            k = idx(sid)
            if k is None or sid in fixed:
                errs.append(f"delete: '{sid}' " + ("is a fixed slide" if sid in fixed else "does not exist"))
                continue
            slides.pop(k)
            ov.pop(sid, None)
        elif op == "add":
            k = idx(str(c.get("after") or ""))
            new = c.get("slide") or {}
            if k is None or not isinstance(new, dict) or not new.get("type") or not isinstance(new.get("content"), dict):
                errs.append("add: needs 'after' (an existing id) and 'slide' {type, content}")
                continue
            base = f"{slides[k]['id']}_new"
            nid, n = base, 2
            while idx(nid) is not None:
                nid, n = f"{base}{n}", n + 1
            ns = {"id": nid, "type": new["type"], "section": slides[k].get("section", ""), "content": new["content"]}
            if new.get("variant"):
                ns["variant"] = new["variant"]
            if new.get("notes"):
                ns["notes"] = new["notes"]
            slides.insert(k + 1, ns)
        elif op == "move":
            k, a = idx(sid), idx(str(c.get("after") or ""))
            if k is None or a is None:
                errs.append(f"move: '{sid}' or after='{c.get('after')}' does not exist")
                continue
            s = slides.pop(k)
            slides.insert(idx(str(c["after"])) + 1, s)
        elif op == "design":
            if idx(sid) is None:
                errs.append(f"design: no slide with id '{sid}'")
                continue
            cur = ov.get(sid)
            cur = {"variant": cur} if isinstance(cur, str) else dict(cur or {})
            e = dict(cur.get("edit") or {})
            for key in ("set", "add"):
                if isinstance(c.get(key), list):
                    e[key] = list(e.get(key) or []) + c[key]
            if c.get("bg"):
                e["bg"] = c["bg"]
            cur["edit"] = e
            ov[sid] = cur
        elif op == "brand":
            if not isinstance(c.get("patch"), dict):
                errs.append("brand: needs 'patch' {colours / fonts / sizes ...}")
                continue
            b0 = dict(ov.get("_brand") or {})
            p = c["patch"]
            if isinstance(p.get("sizes"), dict):
                p = {**p, "sizes": {**(b0.get("sizes") or {}), **p["sizes"]}}
            ov["_brand"] = {**b0, **p}
        else:
            errs.append(f"unknown op '{op}' (use update | delete | add | move | design | brand)")
    return spec, ov, errs


def refine_validate(name: str, spec: dict, ov: dict | None = None, touched: set | None = None) -> list[str]:
    from . import templates, edits, fonts
    d = spec_path(name).parent
    res = check_spec(name, spec, d, slack=1.2)
    errs = [e for e in res["errors"] if "the kick-off asked for" not in e]    # the humans may change the count now
    ov = ov or {}
    bp = ov.get("_brand") or {}
    for k in ("heading_font", "body_font", "mono_font"):
        if bp.get(k) and not fonts.known(bp[k]):
            errs.append(f"brand: font '{bp[k]}' is not installed; use one of {sorted({f for f, _ in {**fonts.FAMILY_FILES, **fonts._registry_files()}})}")
    if not errs and (touched or bp):                  # lay the changed slides out and measure them
        t = token_sections(name)
        b = edits.patch_brand(stg.compose(t), bp)
        deck = with_overrides(content.resolve(spec, d), ov)
        asm = Assembler(b, stg.visual_of(t), stg.motion_of(t), motif=style_motif(b))
        slides = asm.build(deck)
        for s in slides:
            sid = str(s.meta.get("id") or "").split(".")[0]
            fx = (ov.get(sid) or {}).get("edit") if isinstance(ov.get(sid), dict) else None
            if fx:
                errs += [f"{sid}: {p}" for p in getattr(s, "_edit_problems", [])]
        for i in check_deck(slides, b):
            sid = str(slides[i.slide - 1].meta.get("id") or "").split(".")[0]
            if i.level == "error" and i.code in ("overflow", "bounds") and (bp or sid in (touched or set())):
                errs.append(f"{sid}: after your change {i.msg}")
    for s in spec["slides"]:
        v = s.get("variant")
        if v and v not in templates.LIBRARY.get(s["type"], {}):
            errs.append(f"{s['id']}: variant '{v}' does not exist for type {s['type']} "
                        f"(choose one of {list(templates.LIBRARY.get(s['type'], {}))})")
    return errs


def refine_commit(name: str, spec: dict, ov: dict, requests: list[dict], general: str, summary: list[str],
                  not_done: list[str]) -> dict:
    refine_snapshot(name)
    d = spec_path(name).parent
    spec_path(name).write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    (d / "overrides.json").write_text(json.dumps(ov, indent=1, ensure_ascii=False), encoding="utf-8")
    st = load_state(name)
    rf = _refine(st)
    prev = sdir(name) / "deck" / "_prev"                  # the last version's pictures, to show what really changed
    shutil.rmtree(prev, ignore_errors=True)
    if st.get("deck"):
        prev.mkdir(parents=True)
        rf["prev"] = {}
        for i, (sid, p) in enumerate(zip(st["deck"].get("ids") or [], st["deck"]["pngs"])):
            if (ROOT / p).exists():
                shutil.copy2(ROOT / p, prev / f"{i:03d}.png")
                rf["prev"].setdefault(sid, []).append(rel(prev / f"{i:03d}.png"))
    rf["round"] += 1
    rf["approved"] = False
    rf["history"].append({"round": rf["round"], "requests": requests, "general": general, "summary": summary,
                          "not_done": not_done, "time": __import__("time").strftime("%H:%M")})
    st["spec"]["slides"] = len(spec["slides"])
    st.pop("deck", None)
    save_state(name, st)
    return rf


def refine_undo(name: str) -> dict:
    """Back to the version before the last round (the deck has to be rebuilt)."""
    st = load_state(name)
    rf = _refine(st)
    if rf["round"] == 0:
        raise StepError("this is the first version: nothing to undo")
    d = _hist_dir(name, rf["round"] - 1)
    for f in ("deck.json", "overrides.json"):
        dst = spec_path(name).parent / f
        if (d / f).exists():
            shutil.copy2(d / f, dst)
        else:
            dst.unlink(missing_ok=True)
    rf["round"] -= 1
    rf["history"].pop()
    rf["approved"] = False
    st.pop("deck", None)
    save_state(name, st)
    return rf


def refine_approve(name: str, ok: bool = True) -> dict:
    st = load_state(name)
    if ok and "deck" not in st:
        raise StepError("build the deck first")
    rf = _refine(st)
    rf["approved"] = bool(ok)
    save_state(name, st)
    return rf


# ------------------------------------------------------------------ status
STEP_ORDER = ["paper", "brief", "kickoff"] + STAGES[:-1] + ["spec", "motion", "refine", "deck"]


def status(name: str) -> dict:
    st = load_state(name)
    d = sdir(name)
    sp = st.get("spec", {})
    done = {"paper": "paper" in st and bool((st.get("team") or {}).get("members")),
            "brief": (d / "brief.json").exists(), "kickoff": "kickoff" in st,
            "spec": "spec" in st, "deck": "deck" in st and bool((st.get("refine") or {}).get("approved"))}
    done["refine"] = done["deck"]
    for s in STAGES:
        done[s] = s in st["choices"]
    labels = {"paper": "Sources", "brief": "Theme", "kickoff": "Kick-off", "spec": "Content", "deck": "Deck",
              "refine": "Polish",
              **stg.LABELS_HU}
    steps = [{"id": k, "label": labels[k], "done": done[k],
              "options": options_path(name, k).exists() if k in STAGES else None} for k in STEP_ORDER]
    nxt = next((s["id"] for s in steps if not s["done"]), None)
    return {"name": name, "steps": steps, "next": nxt, "choices": st.get("choices", {}), "paper": st.get("paper"),
            "kickoff": st.get("kickoff"), "spec": sp or None, "deck": st.get("deck"),
            "renders": st.get("renders", {}), "tokens": st.get("tokens", {}), "judge": st.get("judge", {}),
            "sources": sources(name), "team": st.get("team"), "team_run": st.get("team_run"), "llm": st.get("llm"),
            "refine": st.get("refine") or {"round": 0, "history": [], "approved": False}}
