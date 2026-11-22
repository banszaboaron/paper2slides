"""sdl-team: the team's content pipeline (GeoRAProject / p2s, LangGraph) driven from the Slide Studio.

The Studio's session supplies everything the content agents need: the documents with their comments,
the team, the kick-off (slide count, talk length, text amount -> character limits). The graph pauses
twice for a human (outline review, content review); the Studio shows those pauses and resumes the run.
At the end the graph's design step imports the slides into the session (sdl.teamlink.render).

Where the p2s repo is: env P2S_ROOT, or "p2s_root" in config.json, or the repo root this studio/ folder
lives in (one-repo layout), or ../GeoRAProject next to a separate slide-design-lab checkout.
Model: the one chosen on the Studio's first screen (session "llm") runs every content role too
(P2S_LLM_PROVIDER / P2S_LLM_MODEL); without a choice, GeoRAProject/config/models.local.yaml decides.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from _common import FastMCP, Context, ROOT, run, svc

mcp = FastMCP("sdl-team", log_level="WARNING")


def _p2s_root() -> Path:
    cfg = {}
    if (ROOT / "config.json").exists():
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    explicit = os.environ.get("P2S_ROOT") or cfg.get("p2s_root")
    if explicit:
        return Path(explicit)
    if (ROOT.parent / "p2s" / "graph.py").exists():        # one repo: <repo>/studio + <repo>/p2s
        return ROOT.parent
    return ROOT.parent / "GeoRAProject"


def _boot():
    """Import p2s with our design backend; keys from the p2s .env and our config.json."""
    root = _p2s_root()
    if not (root / "p2s" / "graph.py").exists():
        raise svc.StepError(f"the team's pipeline was not found at {root} (run the Studio from "
                            f"this repo's studio/ folder, or set p2s_root in config.json)")
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env", override=False)
    except ImportError:
        pass
    os.environ["P2S_DESIGN_BACKEND"] = "design.render:render"     # our design side, always
    os.environ["SDL_ROOT"] = str(ROOT)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        from sdl import agents
        key = agents.config().get("api_key")
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from p2s import runner, tracelog, llm
    return runner, tracelog, llm


def _use_session_llm(session: str | None):
    """Every content role on the model chosen for this session (same as the design agents)."""
    from sdl import agents
    ov = agents.session_llm(session)
    if not ov:
        os.environ.pop("P2S_LLM_PROVIDER", None)
        os.environ.pop("P2S_LLM_MODEL", None)
        return
    prov, alias = ov.get("provider", "anthropic"), ov.get("model", "sonnet")
    os.environ["P2S_LLM_PROVIDER"] = prov
    os.environ["P2S_LLM_MODEL"] = alias if prov == "claude_cli" else agents.API_MODELS.get(alias, alias)


def _human(rec: dict) -> str:
    a, tool = rec.get("agent", ""), rec.get("tool", "")
    if a.startswith("content:"):
        return f"Writing slide {a.split(':', 1)[1]}…"
    if a.startswith("checker:"):
        return f"LLM checker reads the {a.split(':', 1)[1]}…"
    names = {"ingest": "Reading the documents", "outline": "Planning the talk", "validator": "Checking",
             "script": "Writing the presenter script", "design": "Handing the slides to the design side",
             "human": "Your answer was recorded"}
    out = rec.get("output", "")
    return f"{names.get(a, a)} · {tool}" + (f" → {out[:90]}" if a == "validator" else "")


def _with_progress(tracelog, fn, log):
    def listener(rec):
        if log:
            log(_human(rec))
    tracelog.LISTENERS.append(listener)
    try:
        return fn()
    finally:
        tracelog.LISTENERS.remove(listener)


def _view(session: str, st: dict) -> dict:
    """The run's status, trimmed for the Studio."""
    p = (st.get("pending") or [None])[0]
    out = {"run_id": st["run_id"], "state": st["state"], "pending": None, "open_feedback": st.get("open_feedback", []),
           "script": st.get("script")}
    if p:
        out["pending"] = {"kind": p["kind"], "question": p["question"],
                          "options": [{"id": o["id"], "label": o["label"]} for o in p.get("options", [])],
                          "context": {k: v for k, v in (p.get("context") or {}).items() if k != "figures"}}
    s2 = svc.load_state(session)
    s2["team_run"] = {"run_id": st["run_id"], "state": st["state"],
                      "kind": out["pending"]["kind"] if out["pending"] else None}
    svc.save_state(session, s2)
    return out


def _start(session: str, log=None) -> dict:
    runner, tracelog, _ = _boot()
    _use_session_llm(session)
    st = svc.load_state(session)
    if "kickoff" not in st:
        raise svc.StepError("answer the kick-off questions first (slide count, text amount)")
    team = st.get("team") or {}
    if not team.get("members"):
        raise svc.StepError("enter the presenters' names on the Sources step first")
    srcs = svc.sources(session)
    d = svc.sdir(session)
    sources = [{"path": str(d / s["file"]), "role": s["role"], "comment": s.get("comment", ""),
                "title": s.get("title", ""), "text_path": str(svc.source_text_path(session, i))}
               for i, s in enumerate(srcs)]
    unread = [s.get("title") or s["file"] for s in srcs if s.get("scanned") and s.get("read_by") != "claude"]
    if unread:
        raise svc.StepError("these PDFs have no text layer and were not read yet: " + ", ".join(unread) +
                            " – open the Sources step and let Claude read them")
    ko = st["kickoff"]
    extra = {"sources": sources, "n_slides": ko["logical_slides"], "talk_minutes": ko.get("talk_minutes", 20),
             "density": ko.get("density", "medium"), "limits": ko.get("char_limits"),
             "closing": team.get("closing") or {"title": "Thank you", "subtitle": "Questions?"},
             "design": {"session": session}}
    notes = "; ".join(x for x in (ko.get("notes"), ko.get("direction_text") and
                                  f"visual direction: {ko['direction_text']}") if x)
    rid = f"{session}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    res = _with_progress(tracelog, lambda: runner.start(sources[0]["path"], team["members"], team.get("venue", ""),
                                                        notes, run_id=rid, extra=extra), log)
    return _view(session, res)


def _resume(session: str, choice: str, text: str = "", slide_ids: list | None = None, log=None) -> dict:
    runner, tracelog, _ = _boot()
    _use_session_llm(session)
    tr = svc.load_state(session).get("team_run") or {}
    if not tr.get("run_id"):
        raise svc.StepError("no team pipeline run in this session yet")
    ans = {"choice": choice, "text": text or ""}
    if slide_ids:
        ans["slide_ids"] = slide_ids
    res = _with_progress(tracelog, lambda: runner.resume(tr["run_id"], ans), log)
    return _view(session, res)


def _continue(session: str, log=None) -> dict:
    """Run on from the last saved step of a run that stopped with an error (nothing is redone)."""
    runner, tracelog, _ = _boot()
    _use_session_llm(session)
    tr = svc.load_state(session).get("team_run") or {}
    if not tr.get("run_id"):
        raise svc.StepError("no team pipeline run in this session yet")
    st = runner.status(tr["run_id"])
    if st["state"] == "waiting_for_human":
        return _view(session, st)
    if not st.get("next"):
        raise svc.StepError(f"the run is {st['state']}: nothing to continue")

    def go():
        with runner._lock:
            runner.get_graph().invoke(None, runner._cfg(tr["run_id"]))
        return runner.status(tr["run_id"])
    return _view(session, _with_progress(tracelog, go, log))


def _reimport(session: str, log=None) -> dict:
    """The last run's finished slides (deck.json, with the script) into the session again, without running
    the agents: for when only the design side failed at the end."""
    import subprocess
    runner, _, _ = _boot()
    tr = svc.load_state(session).get("team_run") or {}
    if not tr.get("run_id"):
        raise svc.StepError("no team pipeline run in this session yet")
    rd = Path(runner.status(tr["run_id"])["trace"]).parent
    if not (rd / "deck.json").exists():
        raise svc.StepError(f"the run {tr['run_id']} has no slides yet")
    if log:
        log("Importing the slides of the last run…")
    (rd / "_design_sent_back").unlink(missing_ok=True)
    out = rd / "_reimport_result.json"
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "teamlink_cli.py"), "render", str(rd / "deck.json"),
                        str(rd), str(out)], cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not out.exists():
        raise svc.StepError("import failed: " + (p.stderr or p.stdout or "")[-1200:])
    res = json.loads(out.read_text(encoding="utf-8"))
    if not res.get("pptx_path"):
        raise svc.StepError("import refused: " + "; ".join(f"{i['slide_id']}: {i['message']}" for i in res.get("issues", [])))
    return {"state": "done", "run_id": tr["run_id"], "pending": None, "open_feedback": res.get("issues", [])}


def _status(session: str) -> dict:
    runner, _, _ = _boot()
    tr = svc.load_state(session).get("team_run") or {}
    if not tr.get("run_id"):
        return {"state": "none"}
    return _view(session, runner.status(tr["run_id"]))


def _info(session: str | None = None) -> dict:
    try:
        _, _, llm = _boot()
    except svc.StepError as e:
        return {"ready": False, "error": str(e)}
    _use_session_llm(session)
    roles = {r: f"{llm.role_cfg(r)['provider']}:{llm.role_cfg(r).get('model', '')}"
             for r in ("outline", "content", "checker", "script")}
    return {"ready": True, "p2s_root": str(_p2s_root()), "roles": roles,
            "fake": any(v.startswith("fake") for v in roles.values())}


@mcp.tool()
async def team_info(session: str = "") -> dict:
    """Is the team's pipeline installed, and which model does each content role use (for this session)."""
    return await run(None, _info, session or None)


@mcp.tool()
async def team_start(session: str, ctx: Context = None) -> dict:
    """Start the content pipeline for this session (documents, team, kick-off). Runs until the outline review."""
    return await run(ctx, _start, session, log=True)


@mcp.tool()
async def team_resume(session: str, choice: str, text: str = "", slide_ids: list[str] | None = None,
                      ctx: Context = None) -> dict:
    """Answer the pending review: choice approve | revise | abort, text = what to change."""
    return await run(ctx, _resume, session, choice, text, slide_ids, log=True)


@mcp.tool()
async def team_continue(session: str, ctx: Context = None) -> dict:
    """Continue this session's run from its last saved step (after an error), without redoing finished steps."""
    return await run(ctx, _continue, session, log=True)


@mcp.tool()
async def team_reimport(session: str, ctx: Context = None) -> dict:
    """Import the slides of this session's last run again (no agents) – when only the design step failed."""
    return await run(ctx, _reimport, session, log=True)


@mcp.tool()
async def team_status(session: str) -> dict:
    """State of this session's content run (waiting for a review, done, ...) with the pending question."""
    return await run(None, _status, session)


@mcp.tool()
async def team_script(session: str) -> dict:
    """The presenter script (markdown) of the imported content."""
    p = svc.spec_path(session).parent / "script.md"
    if not p.exists():
        raise ValueError("no presenter script yet: run the team pipeline")
    return {"file": svc.rel(p), "markdown": p.read_text(encoding="utf-8")}


if __name__ == "__main__":
    mcp.run()
