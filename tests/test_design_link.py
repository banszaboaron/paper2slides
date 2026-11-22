"""Offline end-to-end run with the slide-design-lab backend (fake models): two documents, kick-off
slide count, fixed frame (title ... credits, pipeline, thank-you), presenter script, rendered deck.
Skipped when slide-design-lab is not next to this repo (or SDL_ROOT)."""
import json
import os
from pathlib import Path

import pymupdf as fitz
import pytest

_HERE = Path(__file__).resolve().parents[1]
SDL = Path(os.environ.get("SDL_ROOT") or (_HERE / "studio" if (_HERE / "studio" / "src" / "sdl").exists()
                                         else _HERE.parent / "slide-design-lab"))
pytestmark = pytest.mark.skipif(not (SDL / "src" / "sdl" / "teamlink.py").exists(),
                                reason="slide-design-lab not found")


def _pdf(path, title, extra=""):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontsize=18)
    page.insert_text((72, 110), ("Lorem ipsum 23.75 and 19.58 " + extra) * 20, fontsize=9)
    page.draw_rect(fitz.Rect(100, 200, 500, 400), color=(0, 0, 1), fill=(0.8, 0.8, 1))
    page.insert_text((72, 420), "Figure 1: Overview of the method.", fontsize=9)
    doc.save(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("P2S_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("P2S_DESIGN_BACKEND", "design.render:render")
    monkeypatch.setenv("SDL_ROOT", str(SDL))
    import importlib, p2s.paths, p2s.tracelog, p2s.llm, p2s.graph, p2s.nodes, p2s.runner
    for m in (p2s.paths, p2s.tracelog, p2s.llm, p2s.nodes, p2s.graph, p2s.runner):
        importlib.reload(m)
    return tmp_path


def test_design_mode_run(env):
    from p2s import runner
    paper, rep = env / "paper.pdf", env / "report.pdf"
    _pdf(paper, "A Tiny Paper About Things")
    _pdf(rep, "Our replication report", "replicated 77.16")
    extra = {"sources": [{"path": str(paper), "role": "paper", "comment": ""},
                         {"path": str(rep), "role": "own_results", "comment": "our replication: 2 slides"}],
             "n_slides": 12, "talk_minutes": 15, "density": "medium",
             "closing": {"title": "Thank you for your attention", "subtitle": ""}}
    st = runner.start(str(paper), ["Alice", "Bob"], "ICLR 2026", run_id="d1", extra=extra)
    ctx = st["pending"][0]["context"]
    ids = [s["id"] for s in ctx["slides"]]
    assert ids[0] == "title" and ids[-3:] == ["credits", "workflow", "thanks"] and len(ids) == 12

    st = runner.resume("d1", "approve")
    assert st["pending"][0]["kind"] == "deck_review"
    st = runner.resume("d1", "approve")
    assert st["state"] == "done" and st["pptx_path"].endswith(".pptx") and Path(st["pptx_path"]).exists()
    script = Path(st["script"]).read_text(encoding="utf-8")
    assert "Presenter script" in script and "Alice:" not in script     # the team decides who says what

    spec = json.loads((env / "runs" / "d1" / "sdl_spec.json").read_text(encoding="utf-8"))
    assert spec["slides"][0]["content"]["authors"].endswith("presented by Alice, Bob")
    assert spec["slides"][-1]["variant"] == "closing_thanks"
    assert spec["slides"][-1]["content"]["title"] == "Thank you for your attention"
    assert spec["slides"][-2]["id"] == "workflow" and spec["slides"][-2]["type"] == "diagram"
    assert any(s["type"] == "section" for s in spec["slides"])
    assert all(s.get("notes") for s in spec["slides"] if s["type"] != "section")


def test_unsourced_numbers():
    from p2s.checks import _source_values, unsourced_numbers
    src = _source_values("accuracy 23.75 vs 19.58, rho 0.2, 18,464,768 parameters")
    assert unsourced_numbers("23.8% vs 19.6 with rho = 20%", src) == []
    assert unsourced_numbers("we reach 31.4", src) == ["31.4"]
    assert unsourced_numbers("3 steps in 2026, 18,464,768 params", src) == []
