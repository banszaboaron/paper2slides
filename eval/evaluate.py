"""Small, re-runnable system evaluation of finished Slide Studio sessions (no LLM calls, no PowerPoint).

    python eval/evaluate.py minerva_single minerva_checked [--out eval/results]

For every session (studio/sessions/<name>/) it measures, from the saved artefacts only:

  grounding   every non-trivial number on the slides is looked up in the source documents (same rule as the
              pipeline's own check: rounding and % <-> fraction allowed). Found numbers still need a human
              look at the CONTEXT (right quantity, right model, right benchmark), so each one is written to
              <out>/<session>_numbers.csv with the source passages around its occurrences and empty
              `verdict` / `reviewer` columns. A number that is found in the source is NOT counted as correct.
  layout      the deck is laid out again with the session's tokens and measured with the chosen fonts
              (sdl.check: overflow, out of bounds, contrast, tiny text ...), counted by level and code
  math        raw sub/superscripts outside $...$ markup in the visible text
  editability the built .pptx is opened with python-pptx: share of the spec's visible text that is native,
              editable PowerPoint text (not baked into pictures), pictures, native equations (OMML)
  cost/time   wall time per step (headless_report.json), agent calls with tokens and the API-equivalent cost
              estimate the Claude Code CLI reports (agents/*.json usage, p2s trace.jsonl)
  human edits the review sheet <out>/<session>_review.csv (one row per slide) is merged in when it has been
              filled in: needs_edit (0/1), minutes, what. Empty cells are reported as "not reviewed".

Results: <out>/results.json and <out>/results.md. Re-running on the same sessions gives the same numbers.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STUDIO = REPO / "studio"
sys.path[:0] = [str(STUDIO / "src"), str(REPO)]

from p2s.checks import _nums, unsourced_numbers, _source_values  # noqa: E402
from p2s.contracts import math_markup_errors  # noqa: E402

MARKUP = re.compile(r"==|!!|\*\*|\$")


def _sources(sd: Path) -> str:
    texts = []
    for p in [sd / "paper" / "text.txt", *sorted((sd / "paper").glob("source_*.txt")), *sorted(sd.glob("sources/*.txt"))]:
        if p.exists():
            texts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(texts)


def _slide_texts(spec: dict) -> list[tuple[str, str, str]]:
    from sdl.content import visible_text
    out = []
    for s in spec["slides"]:
        if s.get("type") in ("credits", "workflow", "pipeline_video"):   # generated frame, not paper content
            continue
        txt = " ".join(visible_text(s.get("content") or {}))
        out.append((str(s.get("id")), s.get("type", ""), txt))
    return out


def _contexts(src: str, raw: str, width: int = 90, k: int = 3) -> list[str]:
    s = raw.replace("−", "-").rstrip("%")
    pat = re.compile(r"(?<![\d.])" + re.escape(s) + r"(?![\d])")
    out = []
    for m in pat.finditer(src):
        a, b = max(0, m.start() - width), min(len(src), m.end() + width)
        out.append(re.sub(r"\s+", " ", src[a:b]).strip())
        if len(out) >= k:
            break
    return out


def grounding(spec: dict, src: str, out_csv: Path, review_prefill: dict | None = None) -> dict:
    vals = _source_values(src)
    rows, total, unsourced = [], 0, []
    for sid, typ, txt in _slide_texts(spec):
        plain = MARKUP.sub("", txt)
        bad = set(unsourced_numbers(plain, vals))
        for v, dec, raw in _nums(plain):
            if dec == 0 and (abs(v) <= 20 or 1900 <= v <= 2100):
                continue                                # same exemption as the pipeline (counts, years)
            m = re.search(r"(?<![\w.])" + re.escape(raw), plain) or re.search(re.escape(raw), plain)
            if m and re.search(r"([A-Za-z]\d*-|@)$", plain[max(0, m.start() - 6):m.start()]):
                continue                                # part of a name, not a quantity: "o1-0912", "cons@64"
            total += 1
            ctx = plain[max(0, m.start() - 70): m.end() + 70] if m else ""
            found = raw not in bad
            if not found:
                unsourced.append({"slide": sid, "number": raw, "slide_context": ctx})
            key = f"{sid}|{raw}"
            pre = (review_prefill or {}).get(key, {})
            rows.append({"slide": sid, "type": typ, "number": raw, "in_source": int(found),
                         "slide_context": ctx, "source_passages": " || ".join(_contexts(src, raw)) if found else "",
                         "verdict": pre.get("verdict", ""), "reviewer": pre.get("reviewer", ""),
                         "note": pre.get("note", "")})
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["slide"])
        w.writeheader()
        w.writerows(rows)
    verdicts = Counter(r["verdict"] for r in rows if r["verdict"])
    return {"numbers_checked": total, "found_in_source": total - len(unsourced), "not_in_source": len(unsourced),
            "not_in_source_list": unsourced, "context_verdicts": dict(verdicts),
            "context_reviewed": sum(verdicts.values()),
            "context_reviewers": sorted({r["reviewer"] for r in rows if r["reviewer"]})}


def math_issues(spec: dict) -> list[dict]:
    out = []
    for sid, _, txt in _slide_texts(spec):
        for e in math_markup_errors(txt):
            out.append({"slide": sid, "error": e})
    return out


def layout(name: str) -> dict:
    from sdl import service as svc
    from sdl.assemble import Assembler
    from sdl.check import check_deck
    from sdl.visuals import style_motif
    b, vl, m = svc.tokens(name)
    slides = Assembler(b, vl, m, motif=style_motif(b)).build(svc.load_spec(name))
    issues = check_deck(slides, b)
    by = Counter(f"{i.level}:{i.code}" for i in issues)
    errs = [{"slide": i.slide, "id": str(slides[i.slide - 1].meta.get("id") or ""), "code": i.code, "msg": i.msg}
            for i in issues if i.level == "error"]
    return {"physical_slides": len(slides), "issues_by_level_code": dict(sorted(by.items())),
            "errors": len(errs), "warnings": sum(1 for i in issues if i.level == "warn"), "error_list": errs}


def editability(pptx: Path, spec: dict) -> dict:
    from pptx import Presentation
    from pptx.util import Emu  # noqa: F401
    prs = Presentation(str(pptx))
    native, pics, omml = [], 0, 0
    for sl in prs.slides:
        for sh in sl.shapes:
            stack = [sh]
            while stack:
                s = stack.pop()
                if s.shape_type == 6:                   # group
                    stack.extend(s.shapes)
                    continue
                if s.shape_type == 13:
                    pics += 1
                if getattr(s, "has_text_frame", False) and s.has_text_frame:
                    native.append(s.text_frame.text)
                xml = s._element.xml if hasattr(s, "_element") else ""
                omml += xml.count("<m:oMath>") + xml.count("<m:oMath ")
    blob = re.sub(r"\s+", " ", " ".join(native)).lower()
    words = [w for _, _, t in _slide_texts(spec) for w in re.findall(r"[A-Za-z]{4,}", MARKUP.sub("", t))]
    hit = sum(1 for w in words if w.lower() in blob)
    return {"pptx": str(pptx.relative_to(REPO)) if pptx.is_relative_to(REPO) else str(pptx),
            "slides_in_pptx": len(prs.slides), "native_text_frames": len(native), "pictures": pics,
            "native_equations_omml": omml, "spec_words": len(words),
            "spec_words_as_native_text": round(hit / len(words), 3) if words else None}


def cost_time(sd: Path) -> dict:
    rep = sd / "headless_report.json"
    steps = json.loads(rep.read_text(encoding="utf-8"))["steps"] if rep.exists() else []
    agent_calls, tin, tout, usd, secs = 0, 0, 0, 0.0, 0.0
    oneshot = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "est_api_cost_usd": 0.0, "seconds": 0.0}
    for f in sorted((sd / "agents").glob("*.json")):
        for u in json.loads(f.read_text(encoding="utf-8")).get("usage") or []:
            if f.stem.startswith("spec-"):            # the one-call content agent (incl. its format retries)
                oneshot["calls"] += 1
                oneshot["tokens_in"] += u.get("tokens_in") or 0
                oneshot["tokens_out"] += u.get("tokens_out") or 0
                oneshot["est_api_cost_usd"] += u.get("est_api_cost_usd") or 0
                oneshot["seconds"] += u.get("seconds") or 0
            agent_calls += 1
            tin += u.get("tokens_in") or 0
            tout += u.get("tokens_out") or 0
            usd += u.get("est_api_cost_usd") or 0
            secs += u.get("seconds") or 0
    team = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "est_api_cost_usd": 0.0, "seconds": 0.0}
    st = json.loads((sd / "session.json").read_text(encoding="utf-8")) if (sd / "session.json").exists() else {}
    run_id = ((st.get("team_run") or {}).get("run_id"))
    tr = REPO / "runs" / str(run_id) / "trace.jsonl" if run_id else None
    if tr and tr.exists():
        for line in tr.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("tool") != "llm":
                continue
            team["calls"] += 1
            team["tokens_in"] += r.get("tokens_in") or 0
            team["tokens_out"] += r.get("tokens_out") or 0
            team["est_api_cost_usd"] += r.get("est_api_cost_usd") or r.get("cost_usd") or 0
            team["seconds"] += (r.get("duration_ms") or 0) / 1000
    by_tool = Counter()
    for s in steps:
        by_tool[f"{s['server']}.{s['tool']}"] += s["seconds"]
    return {"wall_seconds_total": round(sum(s["seconds"] for s in steps), 1),
            "wall_seconds_by_step": {k: round(v, 1) for k, v in by_tool.most_common()},
            "design_agents": {"calls": agent_calls, "tokens_in": tin, "tokens_out": tout,
                              "est_api_cost_usd": round(usd, 3), "seconds": round(secs, 1)},
            "content_pipeline": {k: round(v, 3) if isinstance(v, float) else v for k, v in team.items()},
            "content_step": {k: round(v, 3) if isinstance(v, float) else v
                             for k, v in (team if team["calls"] else oneshot).items()},
            "p2s_run_id": run_id}


def human_review(sd: Path, spec: dict, out_csv: Path) -> dict:
    """Create the per-slide review sheet once; read it back when it has been filled in."""
    ids = [(sid, typ) for sid, typ, _ in _slide_texts(spec)]
    if not out_csv.exists():
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["slide", "type", "needs_edit", "minutes", "what", "reviewer"])
            w.writerows([[sid, typ, "", "", "", ""] for sid, typ in ids])
    rows = list(csv.DictReader(out_csv.open(encoding="utf-8")))
    done = [r for r in rows if r["needs_edit"].strip() != ""]
    return {"slides": len(rows), "reviewed": len(done),
            "need_edit": sum(1 for r in done if r["needs_edit"].strip() == "1"),
            "minutes": round(sum(float(r["minutes"] or 0) for r in done), 1),
            "reviewers": sorted({r["reviewer"] for r in done if r["reviewer"]})}


def evaluate(name: str, out: Path) -> dict:
    from sdl import service as svc
    sd = svc.sdir(name)
    spec = svc.load_spec(name)
    src = _sources(sd)
    prefill = {}
    pf = out / f"{name}_numbers.csv"
    if pf.exists():                                     # keep verdicts that were already filled in
        for r in csv.DictReader(pf.open(encoding="utf-8")):
            if r.get("verdict"):
                prefill[f"{r['slide']}|{r['number']}"] = r
    st = svc.load_state(name)
    pptx = svc.ROOT / st["deck"]["pptx"] if "deck" in st else None
    return {"session": name, "paper": (st.get("paper") or {}).get("file"),
            "content_source": (st.get("spec_source") or (st.get("team") and "team") or "?"),
            "logical_slides": len(spec["slides"]),
            "grounding": grounding(spec, src, pf, prefill),
            "math_markup_errors": math_issues(spec),
            "layout": layout(name),
            "editability": editability(pptx, spec) if pptx and pptx.exists() else None,
            "cost_time": cost_time(sd),
            "human_review": human_review(sd, spec, out / f"{name}_review.csv")}


def _md(results: list[dict]) -> str:
    cols = [r["session"] for r in results]
    rows = [
        ("Logical slides", lambda r: r["logical_slides"]),
        ("Numbers checked", lambda r: r["grounding"]["numbers_checked"]),
        ("… not found in sources", lambda r: r["grounding"]["not_in_source"]),
        ("… context reviewed (by)", lambda r: f'{r["grounding"]["context_reviewed"]} '
                                              f'({", ".join(r["grounding"]["context_reviewers"]) or "-"})'),
        ("… context verdicts", lambda r: ", ".join(f"{k}: {v}" for k, v in r["grounding"]["context_verdicts"].items()) or "-"),
        ("Math markup errors", lambda r: len(r["math_markup_errors"])),
        ("Layout errors (measured)", lambda r: r["layout"]["errors"]),
        ("Layout warnings", lambda r: r["layout"]["warnings"]),
        ("Spec words as native PPTX text", lambda r: (r["editability"] or {}).get("spec_words_as_native_text", "-")),
        ("Native equations (OMML)", lambda r: (r["editability"] or {}).get("native_equations_omml", "-")),
        ("Slides needing a human edit", lambda r: f'{r["human_review"]["need_edit"]}/{r["human_review"]["reviewed"]} reviewed'
                                                 if r["human_review"]["reviewed"] else "not reviewed"),
        ("Content step: LLM calls", lambda r: r["cost_time"]["content_step"]["calls"]),
        ("Content step: model time (s)", lambda r: round(r["cost_time"]["content_step"]["seconds"])),
        ("Content step: API-equivalent cost (USD)", lambda r: round(r["cost_time"]["content_step"]["est_api_cost_usd"], 2)),
        ("Whole run in this session: wall time (s)", lambda r: r["cost_time"]["wall_seconds_total"]),
        ("Whole run in this session: API-equivalent cost (USD)",
         lambda r: round(r["cost_time"]["content_pipeline"]["est_api_cost_usd"]
                         + r["cost_time"]["design_agents"]["est_api_cost_usd"], 2)),
    ]
    out = ["| Metric | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for label, f in rows:
        out.append(f"| {label} | " + " | ".join(str(f(r)) for r in results) + " |")
    return "\n".join(out)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--out", default=str(REPO / "eval" / "results"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import os
    os.chdir(STUDIO)
    res = [evaluate(s, out) for s in a.sessions]
    (out / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "results.md").write_text(_md(res) + "\n", encoding="utf-8")
    print(_md(res))
