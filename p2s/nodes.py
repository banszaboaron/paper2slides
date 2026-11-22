"""Graph nodes. Each agent = one node; human pauses live in their own nodes because
LangGraph re-executes a node from the top when it is resumed after `interrupt()`.

Design mode: when the design backend offers `guide()` (slide-design-lab), the outline uses the design
side's slide types, the content agent fills their `content` slots within the kick-off's character
limits, and the frame of the talk is fixed: title first; credits, the pipeline (workflow) slide and the
thank-you slide last. Without a guide everything works as before (generic title + bullets).
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Optional

from langgraph.graph import END
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError

from . import checks, pdf_tools
from .contracts import (Choice, Deck, Feedback, HumanRequest, Paper, Section, Slide,
                        SlideType)
from .design_bridge import design_hook, render_deck
from .llm import structured_call
from .paths import CONFIG, run_dir
from .state import GraphState
from .tracelog import log_step, timed

MAX_CONTENT_ROUNDS = int(os.environ.get("P2S_MAX_CONTENT_ROUNDS", 2))   # validator -> content
MAX_DESIGN_SENDBACKS = int(os.environ.get("P2S_MAX_DESIGN_SENDBACKS", 1))  # design -> content
MAX_OUTLINE_FIXES = int(os.environ.get("P2S_MAX_OUTLINE_FIXES", 2))     # checker -> outline
MAX_SCRIPT_FIXES = int(os.environ.get("P2S_MAX_SCRIPT_FIXES", 2))       # checker -> script
WORKERS = int(os.environ.get("P2S_CONTENT_WORKERS", 2))
DESIGN_CHECK_S = int(os.environ.get("P2S_DESIGN_CHECK_TIMEOUT", 90))   # a stuck design check must not stop the run


def _with_timeout(fn, seconds: int, default):
    """Run fn in a helper thread; after `seconds` give up (the thread is left behind) and return default."""
    import threading
    box: dict = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001 - reported below
            box["e"] = e
    t = threading.Thread(target=run, daemon=True, name="design-check")
    t.start()
    t.join(seconds)
    if t.is_alive():
        import sys
        import traceback
        frame = sys._current_frames().get(t.ident)
        where = "".join(traceback.format_stack(frame)[-25:]) if frame else "?"
        (run_dir("_watchdog") / f"stack_{int(__import__('time').time())}.txt").write_text(where, encoding="utf-8")
        log_step("_watchdog", "validator", tool="timeout", input=f"{seconds}s", output=where[-300:])
        print(f"[p2s] design check still running after {seconds}s – skipped. It was here:\n{where}", flush=True)
        return default
    if "e" in box:
        raise box["e"]
    return box.get("v", default)

# the frame of every talk: written without an LLM, filled from the documents, the team and the trace
FIXED_HEAD = [("title", SlideType.title, "Title slide: paper, venue, presenters")]
FIXED_TAIL = [("credits", SlideType.credits, "Sources and credits"),
              ("workflow", SlideType.workflow, "How these slides were made (the pipeline)"),
              ("thanks", SlideType.thank_you, "Thank you / questions")]
FIXED_IDS = {i for i, *_ in FIXED_HEAD + FIXED_TAIL}


@lru_cache
def catalog() -> dict:
    return json.loads((CONFIG / "catalog.json").read_text(encoding="utf-8"))


def guide(state: GraphState) -> Optional[dict]:
    """The design side's slide types and limits (None = generic mode)."""
    fn = design_hook("guide")
    return fn(dict(state)) if fn else None


# ---------------------------------------------------------------- schemas
class SlidePlan(BaseModel):
    id: str
    section_id: str
    type: SlideType
    goal: str = Field(description="what this slide must convey, one sentence")
    figure_ids: list[str] = Field(default_factory=list, description="ids from the figure inventory")


class OutlinePlan(BaseModel):
    paper: Paper
    sections: list[Section]
    slides: list[SlidePlan]


class ScriptLine(BaseModel):
    id: str = Field(description="slide id")
    seconds: int = Field(description="speaking time on this slide")
    text: str = Field(description="what to say on this slide, spoken English, first person plural")


class Script(BaseModel):
    slides: list[ScriptLine]


# ---------------------------------------------------------------- ingest
def ingest(state: GraphState) -> dict:
    rid = state["run_id"]
    sources = state.get("sources") or [{"path": state["pdf_path"], "role": "paper", "comment": ""}]
    docs, figs, meta = [], [], None
    for k, src in enumerate(sources):
        pdf = Path(src["path"])
        with timed(rid, "ingest", "pymupdf.text", str(pdf)) as o:
            m = pdf_tools.extract_metadata(pdf)
            text = pdf_tools.extract_text(pdf)
            tp = Path(src["text_path"]) if src.get("text_path") else None
            if tp and tp.exists() and len(tp.read_text(encoding="utf-8").strip()) > len(text.strip()):
                text = tp.read_text(encoding="utf-8")[:120_000]     # e.g. read off a picture-only PDF by Claude
            o["output"] = f"{m['pages']} pages, {len(text)} chars"
        with timed(rid, "ingest", "pymupdf.figures", str(pdf)) as o:
            out = run_dir(rid) / "figures" if k == 0 else run_dir(rid) / "figures" / f"d{k + 1}"
            fs = pdf_tools.extract_figures(pdf, out)
            for f in fs:                                  # ids stay unique across documents
                if k:
                    f["id"] = f"d{k + 1}_{f['id']}"
                f["doc"] = k + 1
            o["output"] = [f["id"] for f in fs]
        meta = meta or m
        title = src.get("title") or m.get("title") or next(
            (ln.strip() for ln in m.get("first_page", "").splitlines() if ln.strip()), pdf.stem)
        docs.append({"title": title[:160], "role": src.get("role") or ("paper" if k == 0 else "other"),
                     "comment": src.get("comment", ""), "text": text, "path": str(pdf)})
        figs += fs
    return {"paper_meta": meta, "paper_text": docs[0]["text"], "figures": figs, "doc_texts": docs,
            "content_rounds": 0, "design_rounds": 0, "feedback": [], "status": "ingested"}


# ---------------------------------------------------------------- outline agent
def _n_body(state: GraphState, g: Optional[dict]) -> Optional[int]:
    n = state.get("n_slides")
    return max(3, n - len(FIXED_HEAD) - len(FIXED_TAIL)) if n else None


def _fake_outline(state: GraphState) -> OutlinePlan:
    meta = state["paper_meta"]
    figs = [f["id"] for f in state.get("figures", []) if f["id"].startswith("fig")]
    first_line = next((ln for ln in meta.get("first_page", "").splitlines() if ln.strip()), "")
    title = meta.get("title") or first_line or "Untitled"
    secs = [Section(id="intro", title="Motivation"), Section(id="method", title="Method"),
            Section(id="results", title="Results"), Section(id="end", title="Takeaways")]
    g = guide(state)
    if g:                                             # design mode: body slides only, design types
        n = _n_body(state, g) or 8
        kinds = [SlideType.statement, SlideType.comparison, SlideType.figure_text if figs else SlideType.diagram,
                 SlideType.diagram, SlideType.formula, SlideType.evidence, SlideType.content]
        secs3 = ["intro", "method", "results"]
        body = [(secs3[min(2, i * 3 // max(1, n - 1))], kinds[i % len(kinds)]) for i in range(n - 1)] + \
            [("end", SlideType.closing)]
        slides = [SlidePlan(id=f"s{i + 1:02d}", section_id=sec, type=t, goal=f"Point {i + 1} of the talk",
                            figure_ids=figs[:1] if t in (SlideType.figure, SlideType.figure_text) else [])
                  for i, (sec, t) in enumerate(body)]
    else:
        slides = [
            SlidePlan(id="s01", section_id="intro", type=SlideType.title, goal="Title slide"),
            SlidePlan(id="s02", section_id="intro", type=SlideType.content, goal="The problem the paper solves"),
            SlidePlan(id="s03", section_id="method", type=SlideType.figure_text, goal="Core method overview",
                      figure_ids=figs[:1]),
            SlidePlan(id="s04", section_id="results", type=SlideType.evidence, goal="Main result vs baselines"),
            SlidePlan(id="s05", section_id="end", type=SlideType.closing, goal="Takeaways and limitations"),
            SlidePlan(id="s06", section_id="end", type=SlideType.thank_you, goal="Thank you"),
        ]
    return OutlinePlan(paper=Paper(title=title[:200], authors=[meta.get("author") or "Unknown"],
                                   venue=state.get("venue") or "TBD"),
                       sections=secs, slides=slides)


OUTLINE_SYSTEM = """You plan a 12-18 slide conference talk about an LLM research paper.
Pick slide types only from the allowed enum. Every figure slide must cite figure ids from the
inventory. Group slides into 4-6 sections in talk order. Slide ids: s01, s02, ..."""

OUTLINE_SYSTEM_DESIGN = """You plan the BODY of a {minutes}-minute conference talk about a research paper.
- EXACTLY {n_body} slides. Do NOT plan the title slide, section dividers, credits, the pipeline slide or the
  thank-you slide: they are added automatically around your slides.
- Slide types: only {types}. The last slide is a `closing` (the 3 takeaways).
- 3-5 sections in talk order; a section title is the short question that section answers
  ("Why is RL fine-tuning different?"). Every slide belongs to a section.
- `figure` / `figure_text` slides cite figure ids from the inventory (figure_ids); use the figures that carry
  the argument, from any document. Results tables are better as `evidence` slides (a chart or a table).
- Use every document the way its comment says. The course requires at least one slide that goes beyond
  the paper (the team's own results, analysis, limitations or questions).
- The goal of each slide is one sentence and names the document it draws on when it is not the paper.
- Slide ids: s01, s02, ... in order.
{types_guide}"""


def _frame(plan: OutlinePlan, g: Optional[dict]) -> OutlinePlan:
    """Fixed head/tail around the agent's body; body ids renumbered s01.. in order."""
    body = [s for s in plan.slides if s.id not in FIXED_IDS and s.type not in
            (SlideType.title, SlideType.credits, SlideType.workflow, SlideType.thank_you)]
    if not g:                                        # generic mode: keep the old plan, only add the tail
        body = [s for s in plan.slides if s.id not in FIXED_IDS and s.type not in
                (SlideType.credits, SlideType.workflow, SlideType.thank_you)]
    secs = plan.sections or [Section(id="main", title="Main")]
    first, last = secs[0].id, secs[-1].id
    out = []
    if g:
        out += [SlidePlan(id=i, section_id=first, type=t, goal=goal) for i, t, goal in FIXED_HEAD]
    for k, s in enumerate(body, 1):
        out.append(s.model_copy(update={"id": f"s{k:02d}"} if g else {}))
    out += [SlidePlan(id=i, section_id=last, type=t, goal=goal) for i, t, goal in FIXED_TAIL]
    return plan.model_copy(update={"slides": out})


def outline_errors(plan: OutlinePlan, state: GraphState, g: Optional[dict]) -> list[str]:
    """Deterministic outline rules (design mode)."""
    if not g:
        return []
    errs = []
    body = [s for s in plan.slides if s.id not in FIXED_IDS]
    n = _n_body(state, g)
    if n and len(body) != n:
        errs.append(f"the body has {len(body)} slides, it must have exactly {n}")
    allowed = set(g.get("body_types") or [])
    bad = sorted({s.type.value for s in body if allowed and s.type.value not in allowed})
    if bad:
        errs.append(f"slide types {bad} are not allowed; use only {sorted(allowed)}")
    figs = {f["id"] for f in state.get("figures", [])}
    for s in body:
        unknown = [f for f in s.figure_ids if f not in figs]
        if unknown:
            errs.append(f"{s.id}: unknown figure ids {unknown}; use ids from the inventory")
        if s.type in (SlideType.figure, SlideType.figure_text) and not [f for f in s.figure_ids if f in figs]:
            errs.append(f"{s.id}: a {s.type.value} slide needs a figure id from the inventory "
                        f"(or use another type)")
    if body and body[-1].type != SlideType.closing:
        errs.append("the last body slide must be the `closing` (takeaways)")
    sec_ids = {x.id for x in plan.sections}
    if not 2 <= len(plan.sections) <= 6:
        errs.append(f"{len(plan.sections)} sections; use 3-5")
    sec_lim = (g.get("limits") or {}).get("section")
    for x in plan.sections:                      # section titles become the divider slides
        if sec_lim and len(x.title) > sec_lim:
            errs.append(f"section '{x.id}' title is {len(x.title)} characters; at most {sec_lim} "
                        f"(it becomes a divider slide): '{x.title}'")
    for s in body:
        if s.section_id not in sec_ids:
            errs.append(f"{s.id}: unknown section {s.section_id}")
    order = [s.section_id for s in body]
    runs = [x for k, x in enumerate(order) if k == 0 or order[k - 1] != x]
    if len(runs) != len(set(runs)):
        errs.append("a section's slides must be consecutive (a section divider is added at every change): "
                    + " → ".join(runs))
    return errs


def _outline_payload(plan: OutlinePlan) -> str:
    return json.dumps({"sections": [s.model_dump() for s in plan.sections],
                       "slides": [s.model_dump(mode="json") for s in plan.slides]}, ensure_ascii=False)


def outline(state: GraphState) -> dict:
    g = guide(state)
    human = [f["message"] for f in state.get("feedback", []) if f.get("slide_id") == "outline"]
    inv = [{k: f.get(k) for k in ("id", "doc", "page", "caption")} for f in state.get("figures", [])]
    docs = checks.documents_brief(state)
    user = (f"Venue: {state.get('venue', '')}\nHuman style notes: {state.get('style_notes', '')}\n"
            f"Human feedback on previous outline: {human}\n" + (docs + "\n" if docs else "") +
            f"\nFigure inventory:\n{json.dumps(inv)}\n\n"
            f"Documents:\n{checks.sources_text(state, 60000)}")
    system = OUTLINE_SYSTEM
    if g:
        system = OUTLINE_SYSTEM_DESIGN.format(minutes=state.get("talk_minutes") or 20,
                                              n_body=_n_body(state, g) or 10,
                                              types=", ".join(g.get("body_types") or []),
                                              types_guide=g.get("outline_guide", ""))
    warns: list[dict] = []
    plan = None
    for rnd in range(MAX_OUTLINE_FIXES + 1):
        plan = structured_call(role="outline", schema=OutlinePlan, system=system, user=user,
                               run_id=state["run_id"], agent="outline", fake=lambda: _fake_outline(state))
        plan = _frame(plan, g)
        errs = outline_errors(plan, state, g)
        if not errs and g:                                # the LLM checker only sees rule-clean outlines
            req = (f"Requirements: {_n_body(state, g)} body slides + fixed title/credits/pipeline/thank-you; "
                   f"{state.get('talk_minutes') or 20}-minute talk.\n" + (docs + "\n" if docs else "") +
                   "Outline:\n" + _outline_payload(plan))
            e2, w2 = checks.llm_review("outline", state, req, sources_budget=30000)
            errs = [f"{e['slide_id']}: {e['message']}" for e in e2]
            warns = w2
        log_step(state["run_id"], "validator", tool="outline_check", input=f"round {rnd + 1}",
                 output=errs or "ok")
        if not errs or rnd == MAX_OUTLINE_FIXES:
            if errs:
                warns += [{"slide_id": "outline", "issue": "other", "message": e, "source": "checker"} for e in errs]
            break
        user += ("\n\nYour previous outline:\n" + _outline_payload(plan) +
                 "\nThe checker rejected it:\n- " + "\n- ".join(errs) + "\nReturn the corrected outline.")
    if state.get("venue"):
        plan.paper.venue = state["venue"]
    return {"outline": plan.model_dump(mode="json"), "feedback": [], "status": "outlined",
            "check_warnings": warns}


# ---------------------------------------------------------------- HITL: outline
def review_outline(state: GraphState) -> Command:
    o = state["outline"]
    req = HumanRequest(
        kind="outline_review",
        question="Approve this outline, or describe what to change?",
        options=[Choice(id="approve", label="Approve outline"),
                 Choice(id="revise", label="Regenerate with my notes")],
        default="approve",
        context={"sections": o["sections"],
                 "slides": [{k: s[k] for k in ("id", "section_id", "type", "goal", "figure_ids")}
                            for s in o["slides"]],
                 "figures": [{k: f.get(k) for k in ("id", "page", "path", "doc", "caption")}
                             for f in state.get("figures", [])],
                 "warnings": state.get("check_warnings", [])},
    )
    ans = _normalise(interrupt(req.model_dump()))
    log_step(state["run_id"], "human", tool="interrupt.outline_review", input=req.question, output=ans)
    if ans["choice"] == "revise":
        fb = Feedback(slide_id="outline", issue="other", message=ans.get("text", ""), source="human").model_dump()
        return Command(update={"feedback": [fb], "feedback_log": [fb]}, goto="outline")
    notes = (state.get("style_notes", "") + "\n" + ans.get("text", "")).strip()
    return Command(update={"style_notes": notes, "check_warnings": []}, goto="write_content")


# ---------------------------------------------------------------- content agent
CONTENT_SYSTEM = """You write ONE slide of a conference talk as a JSON slide spec.
Rules: title is one full claim sentence. Respect the word limits given. Any subscript or
superscript must be in $...$ math markup (e.g. $W_{Geo}$). Figures: use the exact `path` from the
inventory, give caption, a one-sentence `meaning`, `source` like 'Fig. 3, Author et al. 2026', and
when the bullets refer to a part of a figure add a `focus` region (normalised bbox + description).
Charts: mark our method's series role='ours', others 'baseline', and state the claim.
Only use numbers that appear in the paper."""

CONTENT_SYSTEM_DESIGN = """You write ONE slide of a conference talk as a JSON slide spec. The design pipeline lays
it out later, so you decide WHAT is on the slide, never how it looks.
- Fill `content` with exactly the slots of the slide's type (guide below); `variant` only where the guide
  names one. `title` = the slide's claim (the same text as content.title when the type has a title).
  Leave `bullets`, `figures` and `charts` empty: everything goes into `content`.
- Stay under the character limit of the slide type (visible text: every slot except image paths, LaTeX,
  sources and notes). Text amount asked at the kick-off: {density}. Short lines, no paragraphs.
- Numbers only copied from the documents, with the table or figure as `source`. The paper's method is
  'ours' in charts and tables. The team's own results (documents with role own_results) are labelled as
  ours/our replication, never as the paper's.
- Images: the `image` slot is the exact `path` from the figure inventory; `meaning` says what it proves.
- Math in text goes inside $...$ (e.g. $\\pi_\\theta$); never write subscripts as plain text.
- `notes`: 2-3 sentences of what to say that is not on the slide (a script writer turns them into the talk).
- English only.

{types_guide}"""


def _fake_content(plan: SlidePlan, state: GraphState) -> tuple[dict, Optional[str]]:
    figs = {f["id"]: f for f in state.get("figures", [])}
    fig = next((figs[i] for i in plan.figure_ids if i in figs), None)
    t, goal = plan.type, plan.goal
    if t == SlideType.statement:
        return {"text": f"The key claim: =={goal}==.", "source": "Paper"}, "statement_sentence"
    if t == SlideType.comparison:
        return {"title": f"{goal}: two approaches", "columns": [
            {"head": "Baseline", "items": ["Standard setup", "Known limits"]},
            {"head": "Ours", "role": "ours", "items": ["The paper's idea", "Fewer parameters"]}]}, "comparison_two"
    if t in (SlideType.figure, SlideType.figure_text) and fig:
        c = {"title": goal, "image": fig["path"], "meaning": "Shows the method described on this slide."}
        if t == SlideType.figure:
            return c | {"image_name": "!!" + fig["id"], "caption": fig["caption"][:80]}, None
        return c | {"body": ["First point about the figure", "Second point"]}, None
    if t == SlideType.diagram:
        return {"title": goal, "steps": ["Input", {"text": "Our step", "role": "ours"}, "Output"],
                "caption": "Three steps."}, None
    if t == SlideType.formula:
        return {"title": goal, "latex": "y = W x", "terms": [["W", "the weight matrix"]]}, None
    if t == SlideType.evidence:
        return {"title": goal, "chart": {"type": "bar", "categories": ["Base", "LoRA", "Ours"],
                                         "series": [{"name": "Accuracy", "values": [10.0, 20.0, 30.0]}],
                                         "point_roles": ["baseline", "baseline", "ours"], "fmt": "0.0"},
                "takeaway": "Ours is ==best== on this benchmark.", "source": "Table 1"}, "evidence_chart_side"
    if t == SlideType.closing:
        return {"message": "One sentence to remember.", "takeaways": ["First", "Second", "Third"]}, "closing_takeaways"
    return {"title": goal, "body": ["First short point", "Second short point"]}, None


def _fake_slide(plan: SlidePlan, state: GraphState) -> Slide:
    figs = {f["id"]: f for f in state.get("figures", [])}
    p = state["outline"]["paper"]
    if guide(state):
        content, variant = _fake_content(plan, state)
        return Slide(id=plan.id, section_id=plan.section_id, type=plan.type, title="",
                     content=content, variant=variant, notes=f"Explain: {plan.goal}.")
    figures = [
        {"path": figs[i]["path"], "caption": figs[i]["caption"][:200],
         "meaning": "Shows the pipeline described on this slide.",
         "source": f"{i.replace('fig', 'Fig. ')}, {p['authors'][0]} et al.",
         "focus": [{"bbox": [0.0, 0.0, 0.5, 1.0], "label": "left half",
                    "description": "The input stage the first bullet refers to."}]}
        for i in plan.figure_ids if i in figs]
    charts = []
    if plan.type == SlideType.evidence:
        charts = [{"kind": "bar", "x_labels": ["Task A", "Task B"],
                   "series": [{"name": "Ours", "values": [71.2, 64.0], "role": "ours"},
                              {"name": "Baseline", "values": [65.3, 60.1], "role": "baseline"}],
                   "claim": "Placeholder: our method beats the baseline on both tasks."}]
    bullets = [] if plan.type in (SlideType.title, SlideType.thank_you, SlideType.section_divider) else \
        [f"Placeholder point {k} for: {plan.goal}" for k in (1, 2)]
    return Slide(id=plan.id, section_id=plan.section_id, type=plan.type,
                 title=p["title"] if plan.type == SlideType.title else f"{plan.goal}.",
                 bullets=bullets, figures=figures, charts=charts)


def _clean(t: str) -> str:
    return re.sub(r"==|\*\*", "", str(t or "")).strip()


def derive_summary(d: dict) -> dict:
    """title + bullets from the design `content` slots (for the reviews and the generic validator)."""
    c = d.get("content") or {}
    if not c:
        return d
    title = c.get("title") or c.get("text") or c.get("message") or \
        " ".join(str(c.get(k, "")) for k in ("number", "text") if c.get(k))
    bullets: list[str] = []
    for k in ("subtitle", "authors", "body", "takeaways", "lines", "steps", "terms", "columns", "takeaway",
              "caption", "meaning"):
        v = c.get(k)
        if isinstance(v, str):
            bullets.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    bullets.append(x)
                elif isinstance(x, dict) and "items" in x:
                    bullets.append(f"{x.get('head', '')}: " + "; ".join(map(str, x.get("items", []))))
                elif isinstance(x, dict):
                    bullets.append(str(x.get("text", "")))
                elif isinstance(x, (list, tuple)) and len(x) == 2:   # formula terms: [LaTeX symbol, explanation]
                    sym = str(x[0]).strip().strip("$")
                    bullets.append(f"${sym}$: {x[1]}")
    if "chart" in c:
        bullets.append("[chart] " + ", ".join(map(str, (c["chart"] or {}).get("categories", []))))
    if "rows" in c:
        bullets.append(f"[table] {len(c['rows'])} rows")
    return {**d, "title": _clean(title)[:300] or d.get("title", ""), "bullets": [_clean(b) for b in bullets if b]}


def _fit_lines(lines: list[str], limit: Optional[int]) -> list[str]:
    if not limit:
        return lines
    out, n = [], 0
    for ln in lines:
        if n + len(ln) > limit:
            break
        out.append(ln)
        n += len(ln)
    return out


def fixed_slide(plan: SlidePlan, state: GraphState, g: Optional[dict]) -> dict:
    """Title, credits, pipeline and thank-you slides: written from the documents, team and trace."""
    p = state["outline"]["paper"]
    team = state.get("team") or []
    lim = (g or {}).get("limits", {})
    authors = [a.strip() for x in p.get("authors") or [] for a in re.split(r";|,| and ", x) if a.strip()]
    short = (authors[0].split()[-1] + " et al.") if len(authors) > 2 else " & ".join(authors)
    ref = f"{short}, {p.get('title', '')}. {p.get('venue', '')}".strip()
    if plan.id == "title":
        c = {"title": p["title"], "subtitle": p.get("venue") or "",
             "authors": f"{short}" + (f" · presented by {', '.join(team)}" if team else "")}
        d = {"type": "title", "content": c}
    elif plan.id == "credits":
        lines = [f"Paper: {ref}"]
        for k, doc in enumerate(state.get("doc_texts") or [], 1):
            if k > 1:
                lines.append(f"{'Our results' if doc['role'] == 'own_results' else 'Also used'}: {doc['title']}")
        figs_used = sorted({f for s in state["outline"]["slides"] for f in s.get("figure_ids", [])})
        if figs_used:
            lines.append("Figures: " + ", ".join(figs_used) + " from the documents above")
        lines.append("Slides: content agents (LangGraph) + design pipeline (slide-design-lab)")
        if team:
            lines.append("Team: " + ", ".join(team))
        # file names / figure ids like "p2_fig1" are not math: an "_" between letters must not look like a subscript
        lines = [re.sub(r"(?<=[A-Za-z0-9\)\]\}])[_^](?=[\{A-Za-z0-9])", "-", ln) for ln in lines]
        d = {"type": "credits", "content": {"title": "Sources and credits",
                                            "lines": _fit_lines(lines, lim.get("credits"))}}
    elif plan.id == "workflow":
        d = {"type": "workflow", "content": workflow_content(state["run_id"], len(state.get("doc_texts") or [1]))}
    else:                                              # the closing line chosen in the Studio (or the default)
        cl = state.get("closing") or {}
        d = {"type": "thank_you", "variant": "closing_thanks",
             "content": {"title": cl.get("title") or "Thank you",
                         "subtitle": cl.get("subtitle", "Questions?"), "reference": ref}}
    if not g:                                          # generic mode: title + bullets only
        d = derive_summary({**d, "id": plan.id})
        d.pop("content", None)
        d.pop("variant", None)
    return {"id": plan.id, "section_id": plan.section_id, "notes": "", **d,
            **({} if not g else derive_summary({**d}))}


def _model_label(m: str) -> str:
    """'claude_cli:sonnet' -> 'Claude Code (sonnet)': readable, and no '_' that looks like a subscript."""
    prov, _, name = m.partition(":")
    label = {"claude_cli": "Claude Code", "anthropic": "Claude API", "fake": "test stub", "google_genai": "Gemini",
             "openai": "OpenAI-compatible", "ollama": "Ollama", "groq": "Groq"}.get(prov, prov)
    return f"{label} ({name})" if name and name != "fake" else label


def workflow_content(run_id: str, n_docs: int) -> dict:
    """The pipeline slide from the step log (the design side adds its own steps at render time)."""
    path = run_dir(run_id) / "trace.jsonl"
    recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    llm = [r for r in recs if r.get("tool") == "llm"]
    checks_n = sum(1 for r in recs if r.get("agent", "").startswith("checker") or r.get("agent") == "validator")
    models = sorted({_model_label(r["model"]) for r in llm if r.get("model")})
    # a scripted run (studio/tools/headless_run.py) approves the review pauses itself: never claim a person did
    reviews = "auto-approved" if os.environ.get("P2S_AUTO_APPROVE") == "1" else "human reviews"
    return {"title": "How these slides were made",
            "steps": [f"{n_docs} document{'s' if n_docs != 1 else ''} → outline agent",
                      {"text": "Content agents write each slide", "role": "ours"},
                      f"Rule + LLM checkers, {reviews}", "Design agents + our choices", "PowerPoint"],
            "caption": f"LangGraph content pipeline + MCP design servers · {len(llm)} LLM calls, {checks_n} checks"
                       + (f" · {', '.join(models)[:60]}" if models else "")}


def _write_one(plan: SlidePlan, state: GraphState, fb: list[dict]) -> tuple[str, dict, Optional[dict]]:
    g = guide(state)
    if plan.id in FIXED_IDS:
        return plan.id, fixed_slide(plan, state, g), None
    figs = [f for f in state.get("figures", []) if f["id"] in plan.figure_ids]
    prev = state.get("slides", {}).get(plan.id)
    if g:
        limit = (g.get("limits") or {}).get(g.get("type_map", {}).get(plan.type.value, plan.type.value))
        system = CONTENT_SYSTEM_DESIGN.format(density=state.get("density") or "medium",
                                              types_guide=g.get("types", ""))
        lim_txt = f"Character limit for type '{plan.type.value}': {limit} visible characters"
        if (g.get("limits") or {}).get("slide_title"):
            lim_txt += (f"\nThe title must fit on one line: at most {g['limits']['slide_title']} characters "
                        f"(a short claim, not a full sentence with clauses)")
    else:
        system = CONTENT_SYSTEM
        lim_txt = f"Word limits for type '{plan.type.value}': {json.dumps(catalog().get(plan.type.value, {}))}"
    docs = checks.documents_brief(state)
    user = (f"Paper: {json.dumps(state['outline']['paper'])}\nTeam: {state.get('team')}\n"
            f"Talk outline (for context): {json.dumps([(s['id'], s['type'], s['goal']) for s in state['outline']['slides']])}\n"
            f"Slide plan: {plan.model_dump_json()}\n{lim_txt}\nFigure inventory: {json.dumps(figs)}\n"
            f"Human style notes: {state.get('style_notes', '')}\n" + (docs + "\n" if docs else "")
            + (f"Previous version: {json.dumps(prev)}\nFix these issues: {json.dumps(fb)}\n" if fb else "")
            + f"\nDocuments:\n{checks.sources_text(state, 40000)}")
    try:
        s = structured_call(role="content", schema=Slide, system=system, user=user,
                            run_id=state["run_id"], agent=f"content:{plan.id}",
                            fake=lambda: _fake_slide(plan, state))
        # the plan owns identity fields
        s = s.model_copy(update={"id": plan.id, "section_id": plan.section_id, "type": plan.type})
        d = s.model_dump(mode="json")
        return plan.id, derive_summary(d) if g else d, None
    except Exception as e:  # keep going; validator reports it
        err = Feedback(slide_id=plan.id, issue="schema", message=str(e)[:500]).model_dump()
        return plan.id, prev or {"id": plan.id, "section_id": plan.section_id,
                                 "type": plan.type.value, "title": plan.goal}, err


def write_content(state: GraphState) -> dict:
    plans = [SlidePlan(**s) for s in state["outline"]["slides"]]
    fb_by_slide: dict[str, list] = {}
    for f in state.get("feedback", []):
        fb_by_slide.setdefault(f["slide_id"], []).append(f)
    existing = state.get("slides") or {}
    todo = [p for p in plans if p.id not in existing or p.id in fb_by_slide or p.id == "workflow"]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(lambda p: _write_one(p, state, fb_by_slide.get(p.id, [])), todo))
    slides = {sid: s for sid, s, _ in results}
    errs = [e for *_, e in results if e]
    rounds = state.get("content_rounds", 0) + (1 if fb_by_slide else 0)
    return {"slides": slides, "feedback": errs, "content_rounds": rounds, "status": "content_written"}


# ---------------------------------------------------------------- validator (deterministic + LLM checker)
def _words(s: str) -> int:
    return len(s.split())


def check_slide(d: dict) -> list[Feedback]:
    sid = d.get("id", "?")
    try:
        s = Slide.model_validate(d)
    except ValidationError as e:
        issue = "math_markup" if "$" in str(e) else "schema"
        return [Feedback(slide_id=sid, issue=issue, message=str(e)[:600])]
    out: list[Feedback] = []
    if s.content or s.id in FIXED_IDS:  # design slots / the fixed frame: checked by the design side
        return out
    lim = catalog().get(s.type.value, {})
    if lim.get("title_words") and _words(s.title) > lim["title_words"]:
        out.append(Feedback(slide_id=sid, issue="overflow", field="title", limit=lim["title_words"],
                            actual=_words(s.title), message="title too long"))
    if "bullets_max" in lim and len(s.bullets) > lim["bullets_max"]:
        out.append(Feedback(slide_id=sid, issue="too_dense", field="bullets", limit=lim["bullets_max"],
                            actual=len(s.bullets), message="too many bullets"))
    for i, b in enumerate(s.bullets):
        if lim.get("bullet_words") and _words(b) > lim["bullet_words"]:
            out.append(Feedback(slide_id=sid, issue="overflow", field=f"bullets[{i}]",
                                limit=lim["bullet_words"], actual=_words(b), message="bullet too long"))
    if "figures_max" in lim and len(s.figures) > lim["figures_max"]:
        out.append(Feedback(slide_id=sid, issue="too_dense", field="figures", limit=lim["figures_max"],
                            actual=len(s.figures), message="too many figures"))
    if s.type in (SlideType.figure, SlideType.figure_text) and not s.figures:
        out.append(Feedback(slide_id=sid, issue="missing_figure", message="figure slide without a figure"))
    for f in s.figures:
        if not Path(f.path).exists():
            out.append(Feedback(slide_id=sid, issue="bad_figure", field="figures",
                                message=f"figure path not found: {f.path}; use a path from the inventory"))
    return out


def assemble_deck(state: GraphState) -> dict:
    o = state["outline"]
    order = [s["id"] for s in o["slides"]]
    g_limits = (guide(state) or {}).get("limits")
    return {"paper": o["paper"], "team": state.get("team", []), "sections": o["sections"],
            "slides": [state["slides"][i] for i in order if i in state.get("slides", {})],
            "sources": [{k: d[k] for k in ("path", "role", "comment", "title")} for d in state.get("doc_texts") or []],
            "talk_minutes": state.get("talk_minutes"),
            "design": {**(state.get("design") or {}), "density": state.get("density"), "limits": g_limits,
                       "run_id": state["run_id"]}}


def number_feedback(deck: dict, state: GraphState) -> list[dict]:
    src = checks._source_values(" ".join(d["text"] for d in state.get("doc_texts") or
                                         [{"text": state.get("paper_text", "")}]))
    out = []
    for s in deck["slides"]:
        if s["id"] in FIXED_IDS:
            continue
        txt = " ".join(checks.visible_text(s.get("content") or {})) if s.get("content") else \
            " ".join([s.get("title", "")] + s.get("bullets", []))
        bad = checks.unsourced_numbers(txt, src)
        if bad:
            out.append(Feedback(slide_id=s["id"], issue="unfaithful", source="validator",
                                message=f"numbers not found in the documents: {', '.join(bad[:6])}. Copy numbers "
                                        f"exactly from the documents (or state what they are computed from)"
                                ).model_dump())
    return out


def validate(state: GraphState) -> Command:
    deck = assemble_deck(state)
    fb = [f.model_dump() for s in deck["slides"] for f in check_slide(s)]
    fb += [f for f in state.get("feedback", []) if f["issue"] == "schema"
           and not any(x["slide_id"] == f["slide_id"] for x in fb)]
    try:
        Deck.model_validate(deck)
    except ValidationError as e:
        if not fb:
            fb.append(Feedback(slide_id="deck", issue="schema", message=str(e)[:600]).model_dump())
    dcheck = design_hook("check")
    if dcheck and not fb:                         # limits, slots, fit with the chosen fonts
        log_step(state["run_id"], "validator", tool="design.check:start", input=f"{len(deck['slides'])} slides")
        with timed(state["run_id"], "validator", "design.check", f"{len(deck['slides'])} slides") as o:
            dfb = [Feedback.model_validate({**i, "source": i.get("source", "design")}).model_dump()
                   for i in _with_timeout(lambda: dcheck(deck, str(run_dir(state["run_id"]))), DESIGN_CHECK_S, [])]
            o["output"] = [f"{f['slide_id']}:{f['issue']}" for f in dfb] or "ok"
        fb += dfb
    fb += number_feedback(deck, state)
    warns: list[dict] = []
    if not fb and state.get("content_rounds", 0) < MAX_CONTENT_ROUNDS:   # LLM checker on rule-clean slides
        body = {**deck, "slides": [s for s in deck["slides"] if s["id"] not in FIXED_IDS]}
        payload = (f"Kick-off: {state.get('talk_minutes') or 20}-minute talk, text amount "
                   f"'{state.get('density') or 'medium'}'.\n" + checks.documents_brief(state) +
                   "\nSlides:\n" + checks.payload_slides(body, deck["design"].get("limits")))
        errs, warns = checks.llm_review("content", state, payload)
        fb += errs
    (run_dir(state["run_id"]) / "deck.json").write_text(json.dumps(deck, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
    log_step(state["run_id"], "validator", tool="validate", input=f"{len(deck['slides'])} slides",
             output=[f"{f['slide_id']}:{f['issue']}" for f in fb] or "ok")
    upd = {"deck": deck, "feedback": fb, "feedback_log": fb + warns, "check_warnings": warns}
    if fb and state.get("content_rounds", 0) < MAX_CONTENT_ROUNDS:
        return Command(update=upd, goto="write_content")
    return Command(update=upd, goto="review_deck")


# ---------------------------------------------------------------- HITL: deck
def review_deck(state: GraphState) -> Command:
    deck = state["deck"]
    req = HumanRequest(
        kind="deck_review",
        question="Content is ready. Send to design, or request changes (mention slide ids like s04)?",
        options=[Choice(id="approve", label="Send to design"),
                 Choice(id="revise", label="Revise slides"),
                 Choice(id="abort", label="Stop here")],
        default="approve",
        context={"deck_json": str(run_dir(state["run_id"]) / "deck.json"),
                 "slides": [{"id": s["id"], "type": s["type"], "title": s["title"],
                             "bullets": s.get("bullets", [])} for s in deck["slides"]],
                 "unresolved_feedback": state.get("feedback", []),
                 "warnings": state.get("check_warnings", [])},
    )
    ans = _normalise(interrupt(req.model_dump()))
    log_step(state["run_id"], "human", tool="interrupt.deck_review", input=req.question, output=ans)
    if ans["choice"] == "abort":
        return Command(update={"status": "aborted"}, goto=END)
    if ans["choice"] == "revise":
        ids = ans.get("slide_ids") or [s["id"] for s in deck["slides"] if s["id"] in ans.get("text", "")]
        fb = [Feedback(slide_id=i, issue="other", message=ans.get("text", ""), source="human").model_dump()
              for i in ids]
        return Command(update={"feedback": fb, "feedback_log": fb, "content_rounds": 0}, goto="write_content")
    return Command(update={"feedback": []}, goto="script")


# ---------------------------------------------------------------- presenter script agent
SCRIPT_SYSTEM = """You write the presenter script of a {minutes}-minute conference talk given by a team.
For EVERY slide id: how many seconds, and what is worth saying on that slide.
- Spoken English, first person plural ("we"), natural and clear; explain the slide, do not read it aloud.
- Add the context the slide leaves out (from the notes and the documents), never new numbers.
- Total time: about {target} seconds (all slides together). At 130-150 words per minute: seconds ≈ words / 2.3.
- The team decides later who presents which slide: never name a speaker and do not write hand-overs;
  every slide's text must work whoever says it.
- Title slide: introduce the paper and the team ({team}). Last slide: short, invite questions.
- The pipeline slide ("How these slides were made"): describe the steps, but give no counts of calls or
  checks (the caption is updated with the final numbers after you write) and do not say a person reviewed
  or chose anything unless the slide says so."""


def _fake_script(deck: dict, team: list[str], minutes: int) -> Script:
    slides = deck["slides"]
    weights = [min(3.0, max(1.0, len(" ".join([s.get("title", "")] + s.get("bullets", []))) / 60)) for s in slides]
    total = 0.95 * minutes * 60
    secs = [max(20, int(total * w / sum(weights))) for w in weights]
    rows = []
    for s, sec in zip(slides, secs):
        words = max(5, round(sec * 140 / 60))
        base = f"On this slide we explain {s.get('title') or s['type']}. "
        text = (base * (words // max(1, len(base.split())) + 1)).split()[:words]
        rows.append(ScriptLine(id=s["id"], seconds=sec, text=" ".join(text)))
    return Script(slides=rows)


def script(state: GraphState) -> Command:
    deck = state["deck"]
    team = state.get("team") or []
    minutes = state.get("talk_minutes") or 20
    ids = [s["id"] for s in deck["slides"]]
    system = SCRIPT_SYSTEM.format(minutes=minutes, target=int(0.95 * minutes * 60), team=", ".join(team) or "the team")
    slides_txt = json.dumps([{"id": s["id"], "type": s["type"], "title": s.get("title"),
                              "on_slide": s.get("bullets", []), "notes": s.get("notes", "")} for s in deck["slides"]],
                            ensure_ascii=False, indent=0)
    user = f"Slides:\n{slides_txt}\n\nDocuments:\n{checks.sources_text(state, 30000)}"
    warns: list[dict] = []
    sc = None
    for rnd in range(MAX_SCRIPT_FIXES + 1):
        sc = structured_call(role="script", schema=Script, system=system, user=user, run_id=state["run_id"],
                             agent="script", fake=lambda: _fake_script(deck, team, minutes))
        data = sc.model_dump()
        errs = checks.script_errors(data, ids, minutes)
        if not errs:
            payload = "Slides:\n" + slides_txt + "\n\nScript:\n" + json.dumps(data, ensure_ascii=False)
            e2, warns = checks.llm_review("script", state, payload, sources_budget=20000)
            errs = [f"{e['slide_id']}: {e['message']}" for e in e2]
            warns += [{"slide_id": "script", "issue": "other", "message": m, "source": "checker"}
                      for m in checks.script_errors(data, ids, minutes, rate=True)]
        log_step(state["run_id"], "validator", tool="script_check", input=f"round {rnd + 1}", output=errs or "ok")
        if not errs:
            break
        if rnd == MAX_SCRIPT_FIXES:
            warns += [{"slide_id": "script", "issue": "other", "message": e, "source": "checker"} for e in errs]
            break
        user += ("\n\nYour previous script:\n" + json.dumps(data, ensure_ascii=False) +
                 "\nThe checker rejected it:\n- " + "\n- ".join(errs) + "\nReturn the corrected, complete script.")
    rows = {r.id: r for r in sc.slides}
    slides = {}
    for s in deck["slides"]:
        r = rows.get(s["id"])
        if r:
            slides[s["id"]] = {**s, "seconds": r.seconds, "notes": f"[~{r.seconds} s]\n{r.text.strip()}"}
    if "workflow" in slides:                       # the pipeline slide counts the whole run, script included
        wf = slides["workflow"]
        c = {**(wf.get("content") or {}), **workflow_content(state["run_id"], len(state.get("doc_texts") or [1]))}
        slides["workflow"] = derive_summary({**wf, "content": c}) if wf.get("content") else wf
    deck = {**deck, "slides": [slides.get(s["id"], s) for s in deck["slides"]]}
    rd = run_dir(state["run_id"])
    (rd / "script.md").write_text(checks.script_markdown(deck, sc.model_dump(), minutes), encoding="utf-8")
    (rd / "deck.json").write_text(json.dumps(deck, indent=2, ensure_ascii=False), encoding="utf-8")
    return Command(update={"slides": slides, "deck": deck, "script": sc.model_dump(),
                           "check_warnings": warns, "feedback_log": warns}, goto="design")


# ---------------------------------------------------------------- design (teammate's side)
def design(state: GraphState) -> Command:
    rid = state["run_id"]
    with timed(rid, "design", "design_bridge.render", f"{len(state['deck']['slides'])} slides") as o:
        res = render_deck(state["deck"], str(run_dir(rid)))
        o["output"] = {"pptx": res.get("pptx_path"), "issues": len(res.get("issues", []))}
    issues = [Feedback.model_validate({**i, "source": i.get("source", "design")}).model_dump()
              for i in res.get("issues", [])]
    content_issues = [i for i in issues if i["slide_id"] in state["slides"]]
    upd = {"pptx_path": res.get("pptx_path"), "feedback_log": issues}
    if content_issues and state.get("design_rounds", 0) < MAX_DESIGN_SENDBACKS:
        return Command(update={**upd, "feedback": content_issues, "content_rounds": 0,
                               "design_rounds": state.get("design_rounds", 0) + 1}, goto="write_content")
    return Command(update={**upd, "status": "done"}, goto=END)


def _normalise(ans) -> dict:
    """Accept 'approve', {'choice': ...}, or free text from the UI / OpenClaw."""
    if isinstance(ans, dict):
        return {"choice": ans.get("choice", "approve"), **ans}
    s = str(ans).strip()
    low = s.lower()
    for c in ("approve", "abort", "revise"):
        if low == c or low.startswith(c + ":"):
            return {"choice": c, "text": s[len(c) + 1:].strip()}
    return {"choice": "revise", "text": s}
