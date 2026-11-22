"""Start / resume / inspect runs. Shared by the CLI (used by OpenClaw) and the web UI.

A run == a LangGraph thread (thread_id = run_id). When the graph hits interrupt() it
stops and the checkpoint is saved; `resume(run_id, answer)` continues from there,
even from another process or after a restart.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from langgraph.types import Command

from .graph import get_graph
from .paths import RUNS

_lock = threading.Lock()  # one graph step at a time per process (PowerPoint COM is single-threaded)


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


def status(run_id: str) -> dict:
    snap = get_graph().get_state(_cfg(run_id))
    if not snap.values:
        return {"run_id": run_id, "state": "unknown"}
    pending = [{"interrupt_id": i.id, **i.value} for t in snap.tasks for i in t.interrupts]
    v = snap.values
    return {
        "run_id": run_id,
        "state": "waiting_for_human" if pending else ("running" if snap.next else v.get("status", "done")),
        "next": list(snap.next),
        "pending": pending,
        "pptx_path": v.get("pptx_path"),
        "deck_json": str(RUNS / run_id / "deck.json") if (RUNS / run_id / "deck.json").exists() else None,
        "open_feedback": v.get("feedback", []),
        "trace": str(RUNS / run_id / "trace.jsonl"),
        "script": str(RUNS / run_id / "script.md") if (RUNS / run_id / "script.md").exists() else None,
    }


def start(pdf_path: str, team: list[str], venue: str = "", style_notes: str = "",
          run_id: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """extra: optional kick-off fields of GraphState (sources, n_slides, talk_minutes, density, limits,
    closing, design). sources[0] is the paper; pdf_path is kept for the old single-PDF call."""
    pdf = Path(pdf_path).resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    init = {"run_id": run_id, "pdf_path": str(pdf), "team": team, "venue": venue,
            "style_notes": style_notes, "slides": {}, "feedback_log": [], **(extra or {})}
    for s in init.get("sources") or []:
        if not Path(s["path"]).exists():
            raise FileNotFoundError(s["path"])
    with _lock:
        get_graph().invoke(init, _cfg(run_id))
    return status(run_id)


def resume(run_id: str, answer: Any) -> dict:
    """answer: 'approve' | 'abort' | 'revise: <text>' | free text | {'choice':..., 'text':..., 'slide_ids': [...]}"""
    st = status(run_id)
    if st["state"] != "waiting_for_human":
        raise RuntimeError(f"run {run_id} is not waiting for input (state={st['state']})")
    with _lock:
        get_graph().invoke(Command(resume=answer), _cfg(run_id))
    return status(run_id)


def list_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    ids = sorted((p.name for p in RUNS.iterdir() if p.is_dir()), reverse=True)
    return [status(i) for i in ids]
