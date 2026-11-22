"""Brand tokens, visual language and motion settings.

Everything visual that the assembler is allowed to use comes from these three
objects, so that a stage-1/2/3 choice can be swapped without touching layout code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace


DEFAULT_GRAPHICS = {
    "chart": {"gap": 60, "labels": "outside", "axis_line": True, "gridlines": False, "others": "baseline",
              "line_w": 3.5, "markers": False},
    "table": {"header": "line", "stripes": False, "highlight": "tint"},
    "figure": {"frame": "none", "radius": 0.0, "shadow": False},
}


def json_copy(d):
    import json
    return json.loads(json.dumps(d))


def graphics_of(b) -> dict:
    """Brand graphics merged over the defaults (older sessions have none)."""
    g = json_copy(DEFAULT_GRAPHICS)
    for k, v in (getattr(b, "graphics", None) or {}).items():
        if isinstance(v, dict):
            g.setdefault(k, {}).update(v)
    return g


@dataclass(frozen=True)
class Brand:
    key: str
    label: str
    bg: str
    surface: str
    text: str
    muted: str
    ours: str          # semantic: the paper's own method
    baseline: str      # semantic: baselines / context
    highlight: str     # semantic: emphasis, call-outs
    accent2: str       # secondary hue (max one per slide)
    heading_font: str
    body_font: str
    mono_font: str
    heading_bold: bool = True
    sizes: dict = field(default_factory=lambda: {
        "display": 54, "title": 34, "h2": 24, "body": 20, "small": 16,
        "caption": 13, "big": 120, "chrome": 11})
    margin: float = 0.62
    gutter: float = 0.32
    dark: bool = False
    motif: str = "auto"        # auto | grid | grid_sharp | rings | rule | blocks | none
    motif_glyph: str = "§"     # used by the 'rule' motif
    layouts: dict = field(default_factory=dict)   # preferred template variant per slide type, e.g. {"title": "title_center"}
    backdrop: str = "plain"    # plain | band_left | band_top | corner  (quiet surface shape on content slides)
    title_rule: bool = False   # short accent bar under slide titles
    graphics: dict = field(default_factory=lambda: json_copy(DEFAULT_GRAPHICS))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class VisualLanguage:
    key: str
    label: str
    corner: str = "round"          # round | sharp | pill
    radius: float = 0.10           # roundRect adj as fraction
    stroke_w: float = 1.5          # pt
    arrow: str = "solid"           # solid | dashed | none
    arrow_head: str = "triangle"   # triangle | stealth | none
    highlight: str = "box"         # box | underline | marker | glow
    depth: str = "flat"            # flat | shadow
    fill_style: str = "tint"       # tint | outline | solid


@dataclass(frozen=True)
class Motion:
    key: str
    label: str
    mode: str = "basic"            # none | basic | local | world
    morph_ms: int = 1200
    fade_ms: int = 400
    build_ms: int = 450
    max_builds: int = 3
    zoom_max: float = 3.0
    world: dict = field(default_factory=dict)   # world-Morph: {"kind": graph|map|orbit|flow|globe3d|network3d, ...}
    chrome: str = "bar"            # navigation chrome: bar | dots | tabs | rail | none
    title_flow: str = "carry"      # carry: titles glide between slides (Morph) | static: titles fade
    plan: dict = field(default_factory=dict)    # morph director: {"transitions": {slide_id: morph|fade}, ...}
    ground: dict = field(default_factory=dict)  # slide ground: {"kind": solid|gradient|radial|grain|dots|lines|topo|mesh|paper,
                                                #                "section": same|surface|ours|text}


# ---------------------------------------------------------------- stage 0: main styles
STYLES = {
    "A_geometric_minimal": Brand(
        key="A_geometric_minimal", label="Geometric minimal (light)",
        bg="FAFAF7", surface="EEF0F3", text="14161A", muted="6B7280",
        ours="1F5EFF", baseline="A3A9B3", highlight="FF8A00", accent2="0EA5A5",
        heading_font="Inter SemiBold", body_font="Inter", mono_font="JetBrains Mono",
        heading_bold=False),
    "B_neon_dark": Brand(
        key="B_neon_dark", label="Neon tech (dark)",
        bg="0B0D12", surface="161A23", text="F2F4F8", muted="8A93A6",
        ours="22D3EE", baseline="4B5563", highlight="F472B6", accent2="A3E635",
        heading_font="Space Grotesk", body_font="Inter", mono_font="JetBrains Mono",
        dark=True),
    "C_editorial": Brand(
        key="C_editorial", label="Editorial (cream, serif)",
        bg="F6F1E7", surface="EAE3D3", text="1E1B16", muted="756C5E",
        ours="B4442B", baseline="A89F8F", highlight="1F4E5F", accent2="C79A2E",
        heading_font="Fraunces SemiBold", body_font="Source Serif 4", mono_font="JetBrains Mono",
        heading_bold=False),
    "D_bold_blocks": Brand(
        key="D_bold_blocks", label="Bold colour blocks",
        bg="FFFFFF", surface="FFE14D", text="111111", muted="555555",
        ours="5B2EFF", baseline="BDBDBD", highlight="FF3D2E", accent2="00B386",
        heading_font="Space Grotesk", body_font="Inter", mono_font="JetBrains Mono",
        sizes={"display": 60, "title": 40, "h2": 26, "body": 20, "small": 16,
               "caption": 13, "big": 150, "chrome": 11}),
}

# ---------------------------------------------------------------- stage 1: brand variants of the chosen style
def brand_variants(base: Brand) -> dict[str, Brand]:
    """Four identity variants inside the chosen main style (palette + type pairing)."""
    return {
        "1_cobalt_amber": replace(base, key="1_cobalt_amber", label="Cobalt + amber (Inter)"),
        "2_ink_teal": replace(base, key="2_ink_teal", label="Ink + teal (Source Serif headings)",
                              ours="0F766E", highlight="E11D48", accent2="7C3AED",
                              heading_font="Source Serif 4 SemiBold"),
        "3_violet_lime": replace(base, key="3_violet_lime", label="Violet + lime (Space Grotesk)",
                                 ours="6D28D9", highlight="65A30D", accent2="0284C7",
                                 heading_font="Space Grotesk", heading_bold=True),
        "4_graphite_red": replace(base, key="4_graphite_red", label="Graphite + red (high contrast)",
                                  ours="D7263D", highlight="1B998B", accent2="2E294E",
                                  bg="FFFFFF", baseline="B0B3B8"),
    }


# ---------------------------------------------------------------- stage 2: visual language
VISUAL_LANGUAGES = {
    "1_soft_round": VisualLanguage("1_soft_round", "Soft, rounded, tinted",
                                   corner="round", radius=0.14, arrow="solid", highlight="box", fill_style="tint"),
    "2_sharp_technical": VisualLanguage("2_sharp_technical", "Sharp, technical, outlined",
                                        corner="sharp", stroke_w=1.25, arrow="solid", arrow_head="stealth",
                                        highlight="underline", fill_style="outline"),
    "3_pill_dashed": VisualLanguage("3_pill_dashed", "Pills, dashed arrows, marker",
                                    corner="pill", radius=0.5, arrow="dashed", highlight="marker", fill_style="tint"),
    "4_solid_depth": VisualLanguage("4_solid_depth", "Solid blocks, soft shadow",
                                    corner="round", radius=0.08, arrow="none", highlight="glow",
                                    depth="shadow", fill_style="solid"),
}

# ---------------------------------------------------------------- stage 3: motion modes
MOTIONS = {
    "0_none": Motion("0_none", "No Morph (fades)", mode="none"),
    "1_basic": Motion("1_basic", "Basic Morph", mode="basic"),
    "2_local": Motion("2_local", "Basic + local zoom", mode="local"),
    "3_world": Motion("3_world", "World Morph", mode="world", morph_ms=1600),
}
