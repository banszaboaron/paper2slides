"""Checkers between the agents: deterministic rules first, then an LLM reviewer (role "checker").

Every check returns Feedback dicts. Errors go back to the agent that wrote the thing (outline,
content or script); warnings are only shown to the humans at the review pauses.
"""
from __future__ import annotations

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .llm import structured_call


# ---------------------------------------------------------------- sources
def sources_text(state: dict, budget: int) -> str:
    """All documents in one prompt block, each with its role and comment. The budget is split so a
    long paper cannot push the team's own report out of the prompt."""
    docs = state.get("doc_texts") or [{"title": "paper", "role": "paper", "comment": "",
                                       "text": state.get("paper_text", "")}]
    if len(docs) == 1:
        d = docs[0]
        return d["text"][:budget]
    share = budget // len(docs)
    spare = sum(max(0, share - len(d["text"])) for d in docs)
    long = [d for d in docs if len(d["text"]) > share]
    extra = spare // max(1, len(long))
    out = []
    for k, d in enumerate(docs, 1):
        head = f"=== DOCUMENT {k}: {d.get('title') or d['role']} (role: {d['role']})"
        if d.get("comment"):
            head += f" | how to use it: {d['comment']}"
        out.append(head + " ===\n" + d["text"][:share + (extra if d in long else 0)])
    return "\n\n".join(out)


def documents_brief(state: dict) -> str:
    docs = state.get("doc_texts") or []
    if len(docs) <= 1 and not (docs and docs[0].get("comment")):
        return ""
    return "Documents (use each one as its comment says):\n" + "\n".join(
        f"- Document {k} [{d['role']}] {d.get('title', '')}: {d.get('comment') or '(no comment)'}"
        for k, d in enumerate(docs, 1))


# ---------------------------------------------------------------- numbers must come from the sources
_NUM = re.compile(r"(?<![\w.])[-−+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])[-−+]?\d+(?:\.\d+)?%?")


def _nums(text: str) -> list[tuple[float, int, str]]:
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0)
        s = raw.replace("−", "-").replace(",", "").rstrip("%").lstrip("+")
        try:
            v = float(s)
        except ValueError:
            continue
        dec = len(s.split(".")[1]) if "." in s else 0
        out.append((v, dec, raw))
    return out


def _source_values(text: str) -> set[float]:
    return {v for v, _, _ in _nums(text)}


def unsourced_numbers(slide_text: str, src_values: set[float]) -> list[str]:
    """Numbers on a slide that appear nowhere in the documents (allowing rounding and % <-> fraction).
    Small integers (counts, years, section numbers) are not checked."""
    bad = []
    for v, dec, raw in _nums(slide_text):
        if dec == 0 and (abs(v) <= 20 or 1900 <= v <= 2100):
            continue
        ok = any(round(s, dec) == round(v, dec) or round(s * 100, dec) == round(v, dec)
                 or round(s / 100, dec) == round(v, dec) or round(-s, dec) == round(v, dec) for s in src_values)
        if not ok:
            bad.append(raw)
    return bad


def visible_text(x, skip=("image", "image_name", "number_name", "latex", "path", "variant", "role",
                          "point_roles", "region", "builds", "build", "fmt", "type")) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [t for k, v in x.items() if k not in skip for t in visible_text(v, skip)]
    if isinstance(x, (list, tuple)):
        return [t for v in x for t in visible_text(v, skip)]
    return []


# ---------------------------------------------------------------- LLM reviewer
class ReviewIssue(BaseModel):
    slide_id: str = Field(description="slide id, or 'outline' / 'deck' for the whole thing")
    severity: Literal["error", "warning"]
    issue: Literal["overflow", "too_dense", "unclear_claim", "unfaithful", "format", "missing_figure", "other"]
    message: str = Field(description="one concrete sentence: what is wrong and how to fix it")


class Review(BaseModel):
    ok: bool
    issues: list[ReviewIssue] = Field(default_factory=list)


REVIEW_SYSTEM = {
    "outline": """You check the outline of a conference talk before the slides are written.
Errors (must be fixed): a document is not used the way its comment asks; the slide count or types break
the stated requirements; a figure slide without a fitting figure; the story order does not make sense;
no slide that goes beyond the paper (the team's own results, analysis or questions).
Warnings: anything a careful presenter would still improve. Be brief and concrete.""",
    "content": """You check slides written by another agent before a human sees them.
For every slide compare the text with the limits and the documents. Errors (must be fixed):
- a number or claim that is not in the documents, or contradicts them (issue 'unfaithful')
- the text clearly exceeds the amount the kick-off asked for, or a slot is used for the wrong thing
  (e.g. a whole paragraph in a chart takeaway, bullets that are full sentences on a 'tight' deck) ('too_dense')
- the slide type or its content slots do not fit what the slide says ('format')
- the title is a topic instead of a claim ('unclear_claim')
- the team's own results (documents with role own_results) are presented as the paper's, or the reverse ('unfaithful')
Warnings: style, wording, minor redundancy. Do not invent problems; an empty list is fine.""",
    "script": """You check the presenter script of a conference talk (what each team member says per slide).
Errors: the script states a number or claim not on the slide or in the documents; it reads the slide
text aloud word by word instead of explaining it; it names who speaks or hands over to a person (the team
decides that itself); the timing of a slide is clearly unrealistic for its text. Warnings: wording.""",
}


def llm_review(kind: str, state: dict, payload: str, sources_budget: int = 40000) -> tuple[list[dict], list[dict]]:
    """-> (errors, warnings) as Feedback dicts (source 'checker')."""
    user = payload
    if sources_budget:
        user += "\n\nDocuments:\n" + sources_text(state, sources_budget)
    rev = structured_call(role="checker", schema=Review, system=REVIEW_SYSTEM[kind], user=user,
                          run_id=state["run_id"], agent=f"checker:{kind}", fake=lambda: Review(ok=True))
    errs, warns = [], []
    for i in rev.issues:
        fb = {"slide_id": i.slide_id, "issue": i.issue, "message": f"[{kind} checker] {i.message}",
              "source": "checker", "field": None, "limit": None, "actual": None}
        (errs if i.severity == "error" else warns).append(fb)
    return errs, warns


# ---------------------------------------------------------------- presenter script (deterministic part)
WPM_MIN, WPM_MAX = 85, 185         # spoken English; ~130 wpm is a calm talk, slides with pauses go slower


def script_errors(script: dict, slide_ids: list[str], minutes: Optional[int], rate: bool = False) -> list[str]:
    """Blocking problems (missing slides, total time). rate=True: the per-slide speaking-rate notes instead."""
    if rate:
        return _rate_notes(script)
    errs = []
    rows = script.get("slides") or []
    have = [r.get("id") for r in rows]
    missing = [i for i in slide_ids if i not in have]
    if missing:
        errs.append(f"no script for slides {', '.join(missing)}")
    extra = [i for i in have if i not in slide_ids]
    if extra:
        errs.append(f"unknown slide ids {', '.join(map(str, extra))}")
    total = sum(int(r.get("seconds") or 0) for r in rows)
    if minutes and not 0.85 * minutes * 60 <= total <= 1.05 * minutes * 60:
        errs.append(f"the seconds add up to {total} s = {total / 60:.1f} min; the talk is {minutes} min "
                    f"(aim for {int(0.95 * minutes * 60)} s)")
    for r in rows:
        if int(r.get("seconds") or 0) <= 0:
            errs.append(f"{r.get('id')}: seconds must be > 0")
    return errs


def _rate_notes(script: dict) -> list[str]:
    out = []
    for r in script.get("slides") or []:
        sec = int(r.get("seconds") or 0)
        words = len(str(r.get("text", "")).split())
        if sec >= 20 and not WPM_MIN <= words / sec * 60 <= WPM_MAX:
            out.append(f"{r.get('id')}: {words} words in {sec} s = {words / sec * 60:.0f} words/min "
                       f"(a calm talk is ~130; adjust the time or the text when you rehearse)")
    return out


def script_markdown(deck: dict, script: dict, minutes: Optional[int]) -> str:
    rows = {r["id"]: r for r in script.get("slides") or []}
    lines = [f"# Presenter script – {deck['paper']['title']}", ""]
    total = sum(int(r.get("seconds") or 0) for r in rows.values())
    lines.append(f"Talk: {minutes or '?'} min · planned: {total // 60}:{total % 60:02d} · team: "
                 f"{', '.join(deck.get('team') or [])}")
    lines.append("Who presents which slide is up to you; every slide's text works whoever says it.")
    lines.append("")
    t = 0
    for n, s in enumerate(deck["slides"], 1):
        r = rows.get(s["id"])
        if not r:
            continue
        head = re.sub(r"==|\*\*|\$", "", s.get("title") or s["type"])
        lines.append(f"### {n}. {head}  ·  {t // 60}:{t % 60:02d} → {(t + int(r['seconds'])) // 60}:"
                     f"{(t + int(r['seconds'])) % 60:02d} ({r['seconds']} s)")
        lines.append(str(r.get("text", "")).strip())
        lines.append("")
        t += int(r["seconds"])
    return "\n".join(lines)


def payload_slides(deck: dict, limits: dict | None) -> str:
    rows = []
    for s in deck["slides"]:
        c = s.get("content") or {}
        txt = " ".join(visible_text(c)) if c else " ".join([s.get("title", "")] + s.get("bullets", []))
        rows.append({"id": s["id"], "type": s["type"], "variant": s.get("variant"),
                     "content": c or {"title": s.get("title"), "bullets": s.get("bullets")},
                     "visible_chars": len(re.sub(r"==|\*\*|\$", "", txt)),
                     "limit_chars": (limits or {}).get(s["type"]), "notes": s.get("notes", "")})
    return json.dumps(rows, ensure_ascii=False, indent=0)
