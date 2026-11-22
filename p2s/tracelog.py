"""Shared JSONL step log (one line per step). Design reads these to build the workflow slide.

Line format (agreed with design):
  ts, run_id, agent, model, tool, input, output, duration_ms, tokens_in, tokens_out, cost_usd
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .paths import run_dir

_lock = threading.Lock()
LISTENERS: list = []   # callables(rec) – e.g. the Slide Studio shows each step as live progress


def _short(x: Any, n: int = 300) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def log_step(run_id: str, agent: str, *, model: Optional[str] = None, tool: Optional[str] = None,
             input: Any = "", output: Any = "", duration_ms: float = 0.0,
             tokens_in: Optional[int] = None, tokens_out: Optional[int] = None,
             cost_usd: Optional[float] = None, **extra) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "tool": tool,
        "input": _short(input),
        "output": _short(output),
        "duration_ms": round(duration_ms, 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        **extra,
    }
    path: Path = run_dir(run_id) / "trace.jsonl"
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for fn in list(LISTENERS):
        try:
            fn(rec)
        except Exception:  # a broken listener must never break the pipeline
            pass


@contextmanager
def timed(run_id: str, agent: str, tool: str, input: Any = ""):
    """Log a non-LLM step: `with timed(rid, "ingest", "pymupdf", pdf) as out: out["output"] = ...`."""
    box: dict = {"output": ""}
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        log_step(run_id, agent, tool=tool, input=input, output=box["output"],
                 duration_ms=(time.perf_counter() - t0) * 1000)
