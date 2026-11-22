"""Design side entry point (owned by the design teammate): slide-design-lab.

Enable with  P2S_DESIGN_BACKEND=design.render:render  in .env.
Contract: see p2s/design_bridge.py and p2s/contracts.py (`python -m p2s schema`).

The design code lives in studio/ (SDL_ROOT; fallback: a slide-design-lab folder next to this repo).
Three functions are used by the graph:
  guide(state)          our slide types + their content slots + the kick-off's character limits
  check(deck, run_dir)  limits, slots, math, images, measured fit with the chosen fonts -> Feedback dicts
  render(deck, run_dir) -> {"pptx_path", "issues"}; inside a Slide Studio session the deck is imported
                        into the session (the deck-look step follows there), standalone a deck is rendered
check and render run in a child process (fonts, formulas, pptx in a server worker thread hung on Windows).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
SDL_ROOT = Path(os.environ.get("SDL_ROOT") or (_HERE / "studio" if (_HERE / "studio" / "src" / "sdl").exists()
                                         else _HERE.parent / "slide-design-lab"))
CHILD_TIMEOUT = int(os.environ.get("P2S_DESIGN_TIMEOUT", 600))


def _sdl():
    src = str(SDL_ROOT / "src")
    if not (SDL_ROOT / "src" / "sdl").exists():
        raise RuntimeError(f"slide-design-lab not found at {SDL_ROOT} (set SDL_ROOT)")
    if src not in sys.path:
        sys.path.insert(0, src)
    from sdl import teamlink
    return teamlink


def _child(cmd: str, deck: dict, run_dir: str):
    """sdl.teamlink.<cmd>(deck, run_dir) in its own Python process."""
    with tempfile.TemporaryDirectory() as td:
        dp, out = Path(td) / "deck.json", Path(td) / "result.json"
        dp.write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(SDL_ROOT / "tools" / "teamlink_cli.py"), cmd, str(dp), run_dir, str(out)],
                           cwd=str(SDL_ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=CHILD_TIMEOUT,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(f"design {cmd} failed: " + (p.stderr or p.stdout or "")[-1500:])
        return json.loads(out.read_text(encoding="utf-8"))


def guide(state: dict) -> dict:
    return _sdl().guide(state)          # light: no fonts or formulas


def check(deck: dict, run_dir: str) -> list[dict]:
    return _child("check", deck, run_dir)


def render(deck: dict, run_dir: str) -> dict:
    """deck: validated Deck dict (paper, team, sections, slides). run_dir: runs/<run_id>/.

    Return {"pptx_path": str, "issues": [Feedback dicts]}. Only issues that need the content agent
    (overflow with the chosen fonts, limits, slots); layout problems are fixed by our visual judge later.
    """
    return _child("render", deck, run_dir)
