"""Run the whole Slide Studio flow without the browser, through the same MCP servers the web UI uses.

    python tools/headless_run.py --pdf ../examples/papers/minerva_2206.14858.pdf --session minerva --replay \
        --spec ../examples/minerva/deck.json
    python tools/headless_run.py --pdf paper.pdf --session mypaper --llm claude_cli --model sonnet \
        --content team --slides 12 --minutes 15 --pick palette=b,type=a

Every step is one MCP tool call (content / agents / design / deck / office / team servers), so this is an
exact, scriptable replay of what a person does in the UI. The ★ design choices, which are a human's in the
UI, are taken from --pick (stage=key) or, by default, the first option; they are written to the report so
the run is reproducible. Output: sessions/<session>/headless_report.json (+ the deck files of the session).

Content sources (--content):
  spec   import a ready slide spec (--spec, e.g. ../examples/minerva/deck.json)
  team   the LangGraph content pipeline (p2s): outline -> checks -> slides -> checks -> script; both human
         review pauses are auto-approved here and logged as such
  draft  one-call placeholder draft by the design agent (quick, not checked against the sources)
--export runs PowerPoint (Windows + desktop PowerPoint only) for PNG + PDF (+ MP4 with --mp4).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ["palette", "type", "graphics", "visual", "layout"]


def _hub():
    spec = importlib.util.spec_from_file_location("sdl_webapp_server", ROOT / "webapp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Hub()


class Run:
    def __init__(self, hub, session: str):
        self.hub, self.session, self.steps = hub, session, []

    async def __call__(self, server: str, tool: str, **args):
        args = {"session": self.session, **args}
        t0 = time.perf_counter()
        print(f"  -> {server}.{tool} {json.dumps({k: v for k, v in args.items() if k != 'session'})[:100]}", flush=True)
        try:
            res = await self.hub.call(server, tool, args)
            ok, err = True, None
        except Exception as e:                        # keep the record, then stop
            res, ok, err = None, False, str(e)
        dt = round(time.perf_counter() - t0, 2)
        self.steps.append({"server": server, "tool": tool, "args": {k: v for k, v in args.items() if k != "session"},
                           "ok": ok, "seconds": dt, "error": err})
        if not ok:
            raise RuntimeError(f"{server}.{tool} failed: {err}")
        return res


async def design_stages(run, a, picks: dict, report: dict):
    """Sources, theme, kick-off and the five ★ design stages (the ★ deck look comes after the content)."""
    await run("content", "ingest_paper", pdf_path=str(Path(a.pdf).resolve()))
    if not a.replay:
        await run("content", "set_llm", provider=a.llm, model=a.model)
    for src in a.source:
        path, _, rest = src.partition("|")
        role, _, comment = rest.partition("|")
        await run("content", "add_source", pdf_path=str(Path(path).resolve()), role=role or "own_results",
                  comment=comment)
    if a.team:
        await run("content", "set_team", team=[t.strip() for t in a.team.split(",") if t.strip()], venue=a.venue, closing_title="Thank you",
                  closing_subtitle="Questions?")
    await run("agents", "write_brief")
    await run("content", "kickoff", slides=a.slides, density=a.density, minutes=a.minutes)
    for stage in STAGES:
        opts = await run("agents", "propose_options", stage=stage)
        await run("design", "render_options", stage=stage)
        keys = [o["key"] for o in (opts or {}).get("options", [])] or \
               [o["key"] for o in (await run("design", "get_options", stage=stage))["options"]]
        key = picks.get(stage, keys[0])
        await run("design", "choose_option", stage=stage, key=key,
                  by="scripted (--pick)" if stage in picks else "auto: first option")
        report["human_choices"][stage] = {"key": key, "offered": keys,
                                          "by": "--pick" if stage in picks else "default (first option)"}


async def main(a) -> int:
    if a.replay:
        os.environ["SDL_LLM"] = "replay"
        os.environ.setdefault("SDL_REPLAY_DIR", str(ROOT / "replay"))
    os.environ["SDL_NO_BROWSER"] = "1"
    os.environ["P2S_AUTO_APPROVE"] = "1"              # the MCP servers inherit it: slides say who reviewed
    picks = dict(p.split("=", 1) for p in a.pick.split(",") if "=" in p)
    hub = _hub()
    await hub.start()
    missing = [s for s in ("content", "agents", "design", "deck") if s not in hub.sessions]
    if missing:
        print(f"MCP servers failed to start: {missing}", file=sys.stderr)
        return 2
    run = Run(hub, a.session)
    if a.reuse_design:                                # same paper + the same ★ choices: only the content differs
        import shutil
        src, dst = ROOT / "sessions" / a.reuse_design, ROOT / "sessions" / a.session
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("spec", "deck", "agents", "headless_report.json"))
        st = json.loads((dst / "session.json").read_text(encoding="utf-8"))
        st["name"] = a.session
        for k in ("deck", "team_run", "judge", "spec_source", "review", "refine"):   # keep "team" (presenters)
            st.pop(k, None)
        st["choices"].pop("motion", None)
        st.get("renders", {}).pop("motion", None)
        (dst / "session.json").write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    t_start = time.perf_counter()
    report = {"session": a.session, "pdf": a.pdf, "llm": "replay" if a.replay else f"{a.llm}:{a.model}",
              "content": a.content, "reuse_design": a.reuse_design or None, "human_choices": {},
              "auto_approved": [], "steps": run.steps}
    prev = ROOT / "sessions" / a.session / "headless_report.json"
    if a.finish and prev.exists():                    # continue the earlier report: its steps, choices, judge
        old = json.loads(prev.read_text(encoding="utf-8"))
        run.steps[:0] = [s for s in old.get("steps", []) if s.get("ok")]
        report = {**old, "steps": run.steps, "resumed": True}
        report.pop("error", None)
    try:
        if a.finish:                                  # the judge already ran: only its fixes, report, export
            report["judge_fixes"] = await run("deck", "apply_review_fixes", target="deck")
            report["deck_report"] = await run("deck", "deck_report")
            if a.export:
                report["export"] = await run("office", "export_deck", png=True, pdf=True, mp4=a.mp4)
            report["ok"] = True
            return 0
        if a.reuse_design:
            src_rep = ROOT / "sessions" / a.reuse_design / "headless_report.json"
            if src_rep.exists():
                report["human_choices"] = {k: v for k, v in json.loads(src_rep.read_text(encoding="utf-8"))
                                           ["human_choices"].items() if k != "motion"}
        else:
            await design_stages(run, a, picks, report)

        if a.content == "spec":
            res = await run("content", "import_spec", spec_path=str(Path(a.spec).resolve()))
            if not res.get("imported", True):
                raise RuntimeError(f"spec rejected: {res.get('errors')}")
        elif a.content == "draft":
            await run("agents", "draft_placeholder_spec")
        else:
            st = await run("team", "team_start")
            for _ in range(6):                       # outline review, content review (+ re-asks)
                pend = (st or {}).get("pending")           # the pending human review, or None
                if not pend:
                    break
                report["auto_approved"].append({"kind": pend.get("kind"),
                                                "open_feedback": (st or {}).get("open_feedback") or []})
                st = await run("team", "team_resume", choice="approve", text="", slide_ids=None)
            report["team"] = {k: st.get(k) for k in ("state", "run_id", "open_feedback")} if isinstance(st, dict) else st

        opts = await run("agents", "propose_options", stage="motion")
        await run("design", "render_options", stage="motion")
        keys = [o["key"] for o in (opts or {}).get("options", [])]
        key = picks.get("motion", keys[0] if keys else "a")
        await run("design", "choose_option", stage="motion", key=key,
                  by="scripted (--pick)" if "motion" in picks else "auto: first option")
        report["human_choices"]["motion"] = {"key": key, "offered": keys,
                                             "by": "--pick" if "motion" in picks else "default (first option)"}

        report["build"] = await run("deck", "build_deck", video=a.video)
        if a.judge:
            report["judge"] = await run("agents", "review_deck", target="deck")
            report["judge_fixes"] = await run("deck", "apply_review_fixes", target="deck")
        report["deck_report"] = await run("deck", "deck_report")
        if a.export:
            report["export"] = await run("office", "export_deck", png=True, pdf=True, mp4=a.mp4)
        report["ok"] = True
    except Exception as e:
        report["ok"], report["error"] = False, str(e)
        print(f"STOPPED: {e}", file=sys.stderr)
    finally:
        report["seconds_total"] = round(time.perf_counter() - t_start, 1)
        out = ROOT / "sessions" / a.session / "headless_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"report: {out}")
        try:
            await asyncio.wait_for(hub.stop(), timeout=3)
        except BaseException:
            pass
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--replay", action="store_true", help="offline: design-agent answers from replay/<session>/")
    ap.add_argument("--llm", default="claude_cli", choices=["claude_cli", "anthropic"])
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--content", default="spec", choices=["spec", "team", "draft"])
    ap.add_argument("--spec", default="")
    ap.add_argument("--source", action="append", default=[], metavar="PDF|ROLE|COMMENT")
    ap.add_argument("--team", default="")
    ap.add_argument("--venue", default="")
    ap.add_argument("--slides", type=int, default=13)
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--density", default="medium", choices=["tight", "medium", "dense"])
    ap.add_argument("--pick", default="", help="human design choices, e.g. palette=b,type=a,motion=c")
    ap.add_argument("--reuse-design", default="", metavar="SESSION",
                    help="copy the paper, theme, kick-off and ★ choices of SESSION; only content + deck look run")
    ap.add_argument("--finish", action="store_true",
                    help="resume a session whose judge already ran: apply its fixes, report, export")
    ap.add_argument("--judge", action="store_true", help="visual judge on the rendered slides + its fixes")
    ap.add_argument("--video", action="store_true", help="append the pipeline video slide")
    ap.add_argument("--export", action="store_true", help="PowerPoint COM export: PNG + PDF")
    ap.add_argument("--mp4", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
