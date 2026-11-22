"""Offline end-to-end test with the `fake` provider: run -> 2 interrupts -> deck."""
import json

import pymupdf as fitz
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("P2S_RUNS_DIR", str(tmp_path / "runs"))
    import importlib, p2s.paths, p2s.tracelog, p2s.llm, p2s.graph, p2s.nodes, p2s.runner
    for m in (p2s.paths, p2s.tracelog, p2s.llm, p2s.nodes, p2s.graph, p2s.runner):
        importlib.reload(m)
    return tmp_path


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A Tiny Paper About Things", fontsize=18)
    page.insert_text((72, 110), "Lorem ipsum " * 40, fontsize=9)
    page.draw_rect(fitz.Rect(100, 200, 500, 400), color=(0, 0, 1), fill=(0.8, 0.8, 1))
    page.insert_text((72, 420), "Figure 1: Overview of the method.", fontsize=9)
    doc.save(path)


def test_contract_math_rule():
    from p2s.contracts import math_markup_errors
    assert math_markup_errors("uses $W_{Geo}$ weights") == []
    assert math_markup_errors("uses W_Geo weights")
    assert math_markup_errors("x^2 grows")


def test_full_run(env):
    from p2s import runner
    pdf = env / "paper.pdf"
    _make_pdf(pdf)

    st = runner.start(str(pdf), ["Alice", "Bob"], "ICLR 2026", run_id="t1")
    assert st["state"] == "waiting_for_human"
    assert st["pending"][0]["kind"] == "outline_review"

    st = runner.resume("t1", "revise: add a slide on limitations")
    assert st["pending"][0]["kind"] == "outline_review"   # regenerated, asks again

    st = runner.resume("t1", {"choice": "approve"})
    assert st["pending"][0]["kind"] == "deck_review"

    st = runner.resume("t1", "approve")
    assert st["state"] == "done"
    assert st["pptx_path"].endswith(".pptx")

    deck = json.loads(open(st["deck_json"], encoding="utf-8").read())
    from p2s.contracts import Deck
    Deck.model_validate(deck)

    lines = [json.loads(l) for l in open(st["trace"], encoding="utf-8")]
    assert {"ts", "agent", "model", "tool", "duration_ms"} <= set(lines[0])
    assert any(l["agent"] == "human" for l in lines)
