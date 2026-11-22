"""LangGraph wiring.

ingest -> outline -> [review_outline]* -> write_content <-> validate -> [review_deck]* -> script -> design -> END
 (n docs)  (+checker)    |  revise -> outline        ^   (rules, design      |  revise -> write_content
                                                     |    check, numbers,    (presenter script
                                                     |    LLM checker)        + checker)
                                                     +---- design issues (max 1 send-back)
* = human pause via interrupt(); state is checkpointed in runs/checkpoints.sqlite
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .paths import CHECKPOINT_DB, RUNS
from .state import GraphState


def build_graph(checkpointer=None):
    g = StateGraph(GraphState)
    g.add_node("ingest", nodes.ingest)
    g.add_node("outline", nodes.outline)
    g.add_node("review_outline", nodes.review_outline, destinations=("outline", "write_content"))
    g.add_node("write_content", nodes.write_content)
    g.add_node("validate", nodes.validate, destinations=("write_content", "review_deck"))
    g.add_node("review_deck", nodes.review_deck, destinations=("write_content", "script", END))
    g.add_node("script", nodes.script, destinations=("design",))
    g.add_node("design", nodes.design, destinations=("write_content", END))

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "outline")
    g.add_edge("outline", "review_outline")
    g.add_edge("write_content", "validate")
    return g.compile(checkpointer=checkpointer)


@lru_cache
def get_graph():
    RUNS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    return build_graph(SqliteSaver(conn))
