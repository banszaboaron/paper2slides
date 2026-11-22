"""Shared contracts between the content side (this graph) and the design side.

These Pydantic models ARE the slide spec. Export the JSON Schema with
`python -m p2s schema > contracts/slide_spec.schema.json` and share it with design.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SlideType(str, Enum):
    title = "title"
    content = "content"
    figure = "figure"
    figure_text = "figure_text"
    comparison = "comparison"
    big_number = "big_number"
    formula = "formula"
    section_divider = "section_divider"
    closing = "closing"
    thank_you = "thank_you"
    evidence = "evidence"
    credits = "credits"
    workflow = "workflow"
    # design-side types (slide-design-lab): written with type-specific `content` slots
    statement = "statement"
    diagram = "diagram"


class Source(BaseModel):
    """One input document. The first one is the paper being presented; the others (e.g. the
    team's replication report) come with a comment that says how to use them."""
    path: str
    role: str = Field(default="paper", description="paper | own_results | notes | other")
    comment: str = Field(default="", description="how the talk should use this document")
    title: str = ""


# --- math markup -----------------------------------------------------------
# Subscripts/superscripts must be inside $...$ (e.g. $W_{Geo}$). Anything like
# W_Geo or x^2 outside of math markup is rejected, as agreed with design.
_MATH_SEGMENT = re.compile(r"\$[^$]+\$")
_BARE_SCRIPT = re.compile(r"[A-Za-z0-9\)\]\}][_^][\{A-Za-z0-9]")


def math_markup_errors(text: str) -> list[str]:
    if text.count("$") % 2:
        return ["unbalanced $ math delimiters"]
    stripped = _MATH_SEGMENT.sub("", text)
    return [f"sub/superscript outside $...$ near '{m.group(0)}'" for m in _BARE_SCRIPT.finditer(stripped)]


def _check_math(v: str) -> str:
    errs = math_markup_errors(v)
    if errs:
        raise ValueError("; ".join(errs))
    return v


# --- deck level --------------------------------------------------------------
class Paper(BaseModel):
    title: str
    authors: list[str]
    venue: str = Field(description="e.g. 'ICLR 2026 (Oral)'")
    year: Optional[int] = None
    url: Optional[str] = None


class Section(BaseModel):
    id: str
    title: str


# --- figures & charts --------------------------------------------------------
class FocusRegion(BaseModel):
    """A specific part of a figure that the slide text refers to."""
    bbox: tuple[float, float, float, float] = Field(
        description="[x0, y0, x1, y1] normalised to 0..1, origin top-left of the image")
    label: str = Field(description="short name, e.g. 'left panel', 'red curve'")
    description: str = Field(description="what is there and why the slide points at it")

    @field_validator("bbox")
    @classmethod
    def _bbox_ok(cls, v):
        x0, y0, x1, y1 = v
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("bbox must be normalised [x0,y0,x1,y1] with x0<x1, y0<y1 in 0..1")
        return v


class Figure(BaseModel):
    path: str = Field(description="PNG cropped from the paper PDF: absolute path, or relative to run_dir")
    caption: str
    meaning: str = Field(description="one sentence: what this figure explains on this slide")
    source: str = Field(description="e.g. 'Fig. 3, Author et al. 2026'")
    focus: list[FocusRegion] = Field(default_factory=list)


class Series(BaseModel):
    name: str
    values: list[float]
    role: Literal["ours", "baseline"]


class Chart(BaseModel):
    kind: Literal["bar", "line", "scatter"] = "bar"
    x_labels: list[str]
    series: list[Series]
    claim: str = Field(description="one sentence: the claim this chart makes")
    y_label: Optional[str] = None
    source: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self):
        for s in self.series:
            if len(s.values) != len(self.x_labels):
                raise ValueError(f"series '{s.name}' has {len(s.values)} values, expected {len(self.x_labels)}")
        if not any(s.role == "ours" for s in self.series):
            raise ValueError("chart needs at least one series with role='ours'")
        return self


# --- slides ------------------------------------------------------------------
class Slide(BaseModel):
    id: str
    section_id: str
    type: SlideType
    title: str = Field(description="ideally one full claim sentence")
    bullets: list[str] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    formula: Optional[str] = Field(default=None, description="LaTeX inside $...$")
    big_number: Optional[str] = None
    notes: str = Field(default="", description="speaker notes")
    # design-side slots (slide-design-lab). When `content` is set it is the source of truth for the
    # slide text and layout; title/bullets above are then a derived summary for review screens.
    content: dict = Field(default_factory=dict, description="type-specific slots, see the design guide")
    variant: Optional[str] = Field(default=None, description="optional layout variant of the type")
    zoom: list[dict] = Field(default_factory=list, description="zoom regions on the main figure")
    seconds: Optional[int] = Field(default=None, description="planned speaking time")

    @field_validator("title")
    @classmethod
    def _title_math(cls, v):
        return _check_math(v)

    @field_validator("bullets")
    @classmethod
    def _bullets_math(cls, v):
        for b in v:
            _check_math(b)
        return v


class Deck(BaseModel):
    paper: Paper
    team: list[str]
    sections: list[Section]
    slides: list[Slide]
    sources: list[Source] = Field(default_factory=list)
    talk_minutes: Optional[int] = None
    design: dict = Field(default_factory=dict, description="hand-over data for the design side (session, limits)")

    @model_validator(mode="after")
    def _refs(self):
        ids = {s.id for s in self.sections}
        for sl in self.slides:
            if sl.section_id not in ids:
                raise ValueError(f"slide {sl.id} references unknown section {sl.section_id}")
        return self


# --- feedback loop (validator / design judge -> content) ---------------------
class Feedback(BaseModel):
    slide_id: str
    issue: Literal["overflow", "too_dense", "math_markup", "missing_figure",
                   "bad_figure", "unclear_claim", "schema", "unfaithful", "format", "other"]
    field: Optional[str] = None
    limit: Optional[int] = None
    actual: Optional[int] = None
    message: str
    source: Literal["validator", "design", "judge", "human", "checker"] = "validator"


# --- human-in-the-loop payload ----------------------------------------------
class Choice(BaseModel):
    id: str
    label: str
    preview_path: Optional[str] = None


class HumanRequest(BaseModel):
    """Value passed to langgraph `interrupt()`; rendered by the UI / OpenClaw."""
    kind: str
    question: str
    options: list[Choice] = Field(default_factory=list)
    default: Optional[str] = None
    allow_free_text: bool = True
    context: dict = Field(default_factory=dict)
