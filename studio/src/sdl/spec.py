"""Physical slide spec: the only thing the renderer reads.

Units are inches on a 13.333 x 7.5 canvas. A logical slide may expand into several
physical slides (zoom steps); each physical slide is regenerated from its own spec,
because python-pptx cannot duplicate slides.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

W, H = 13.333, 7.5


@dataclass
class El:
    kind: str                       # text | rect | ellipse | line | image | chart | formula
    x: float
    y: float
    w: float
    h: float
    name: str = ""                  # "!!name" makes it a Morph pair key
    text: str = ""                  # **bold** and ==accent== inline markup
    style: dict = field(default_factory=dict)
    build: int | None = None        # click-build step (1..3), None = static
    role: str = "content"           # content | chrome | decoration | motif | world
    meaning: str = ""               # what the image explains (required for images, see brief #5)
    data: Any = None                # chart data / image path / latex

    @property
    def box(self):
        return (self.x, self.y, self.w, self.h)


@dataclass
class Slide:
    kind: str                       # title | content | figure | figure_text | comparison | statement | formula | section | closing | evidence | credits
    elements: list[El] = field(default_factory=list)
    bg: str | None = None
    transition: str = "fade"        # morph | fade | none
    notes: str = ""
    meta: dict = field(default_factory=dict)

    def add(self, *els: El):
        self.elements.extend(els)
        return self

    def to_dict(self):
        return asdict(self)
