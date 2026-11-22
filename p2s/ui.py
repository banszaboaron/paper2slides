"""Small local web page for human choices (python -m p2s ui -> http://127.0.0.1:8766).

Lists runs, shows the pending interrupt of the selected run, sends the answer back
with Command(resume=...). Resuming runs in a background thread; the page polls.
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from . import runner
from .paths import RUNS

app = FastAPI(title="p2s choices")
_busy: dict[str, str] = {}


@app.get("/api/runs")
def runs():
    return [{**r, "busy": r["run_id"] in _busy} for r in runner.list_runs()]


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    return {**runner.status(run_id), "busy": run_id in _busy, "error": _busy.get(f"{run_id}:err")}


@app.post("/api/runs/{run_id}/resume")
def resume(run_id: str, answer: dict = Body(...)):
    if run_id in _busy:
        raise HTTPException(409, "run is already resuming")
    _busy[run_id] = "resuming"
    _busy.pop(f"{run_id}:err", None)

    def work():
        try:
            runner.resume(run_id, answer)
        except Exception as e:  # surfaced in the page
            _busy[f"{run_id}:err"] = repr(e)
        finally:
            _busy.pop(run_id, None)

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.get("/files/{run_id}/{path:path}")
def files(run_id: str, path: str):
    base = (RUNS / run_id).resolve()
    p = (base / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
