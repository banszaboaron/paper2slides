from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


def merge_dict(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


def replace_feedback(a: list, b: list) -> list:
    """Feedback is replaced wholesale each validation round; history goes to `feedback_log`."""
    return b


class GraphState(TypedDict, total=False):
    run_id: str
    pdf_path: str
    team: list[str]
    venue: str
    style_notes: str                                 # free text from humans, shared to all agents

    # kick-off (from the Slide Studio or the CLI); all optional, defaults keep the old behaviour
    sources: list[dict]                              # [{path, role, comment, title}], sources[0] = the paper
    n_slides: int                                    # logical slides without section dividers
    talk_minutes: int
    density: str                                     # tight | medium | dense (text amount)
    limits: dict                                     # slide type -> max visible characters (design side)
    closing: dict                                    # last slide: {"title": "Thank you", "subtitle": "Questions?"}
    design: dict                                     # hand-over to the design side, e.g. {"session": ...}
    script: dict                                     # presenter script: {slides: [{id, seconds, text}]}
    doc_texts: list[dict]                            # ingest: [{title, role, comment, text}] per source
    check_warnings: list[dict]                       # checker warnings shown at the human reviews

    # ingest
    paper_meta: dict
    paper_text: str
    figures: list[dict]                              # extracted figure inventory

    # outline
    outline: dict                                    # OutlinePlan as dict

    # content
    slides: Annotated[dict, merge_dict]              # slide_id -> Slide dict
    deck: Optional[dict]                             # assembled Deck dict

    # feedback loop
    feedback: Annotated[list, replace_feedback]      # open Feedback dicts
    feedback_log: Annotated[list, operator.add]      # all feedback ever raised
    content_rounds: int
    design_rounds: int

    # design
    pptx_path: Optional[str]
    status: str
