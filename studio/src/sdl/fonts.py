"""Text measurement with the real font files (fontTools advance widths).

Used by the template library (capacity) and by the overflow check, so both agree
on what "fits" means. PowerPoint wraps on spaces like this greedy wrapper.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"

FAMILY_FILES = {
    ("Inter", False): "Inter-Regular.ttf",
    ("Inter", True): "Inter-Bold.ttf",
    ("Inter SemiBold", False): "Inter-SemiBold.ttf",
    ("Inter SemiBold", True): "Inter-Bold.ttf",
    ("Space Grotesk", False): "SpaceGrotesk-Regular.ttf",
    ("Space Grotesk", True): "SpaceGrotesk-Bold.ttf",
    ("Fraunces", False): "Fraunces-Regular.ttf",
    ("Fraunces SemiBold", False): "Fraunces-SemiBold.ttf",
    ("Fraunces SemiBold", True): "Fraunces-SemiBold.ttf",
    ("JetBrains Mono", False): "JetBrainsMono-Regular.ttf",
    ("JetBrains Mono", True): "JetBrainsMono-Bold.ttf",
    ("Source Serif 4", False): "SourceSerif4-Regular.ttf",
    ("Source Serif 4", True): "SourceSerif4-SemiBold.ttf",
    ("Source Serif 4 SemiBold", False): "SourceSerif4-SemiBold.ttf",
    ("Source Serif 4 SemiBold", True): "SourceSerif4-SemiBold.ttf",
}

MARKUP = re.compile(r"\*\*|==")


class FontMetrics:
    def __init__(self, path: Path):
        f = TTFont(str(path))
        self.upm = f["head"].unitsPerEm
        self.cmap = f.getBestCmap()
        self.hmtx = f["hmtx"].metrics
        hhea = f["hhea"]
        os2 = f["OS/2"]
        # PowerPoint uses the Windows metrics for single line spacing
        self.line = (os2.usWinAscent + os2.usWinDescent) / self.upm
        self.ascent = os2.usWinAscent / self.upm
        self.fallback = self.hmtx.get(self.cmap.get(ord("n"), ".notdef"), (self.upm // 2, 0))[0]
        del hhea

    def width_pt(self, text: str, size: float) -> float:
        total = 0
        for ch in text:
            g = self.cmap.get(ord(ch))
            total += self.hmtx[g][0] if g in self.hmtx else self.fallback
        return total / self.upm * size


def _registry_files() -> dict:
    """Families fetched on demand (fontfetch.ensure_family) live in fonts/registry.json."""
    import json
    reg = FONT_DIR / "registry.json"
    out = {}
    if reg.exists():
        for fam, d in json.loads(reg.read_text(encoding="utf-8")).items():
            out[(fam, False)] = d["regular"]
            out[(fam, True)] = d.get("bold", d["regular"])
    return out


def known(family: str) -> bool:
    return (family, False) in FAMILY_FILES or (family, False) in _registry_files()


def metrics(family: str, bold: bool = False) -> FontMetrics:
    table = {**FAMILY_FILES, **_registry_files()}
    fn = table.get((family, bold)) or table.get((family, False)) or "Inter-Regular.ttf"
    return _metrics_file(fn)


@lru_cache(maxsize=None)
def _metrics_file(fn: str) -> FontMetrics:
    return FontMetrics(FONT_DIR / fn)


def strip_markup(text: str) -> str:
    from .mathtext import strip_inline, typography
    return MARKUP.sub("", typography(strip_inline(text)))


def wrap(text: str, family: str, size: float, width_in: float, bold: bool = False) -> list[str]:
    m = metrics(family, bold)
    width_pt = width_in * 72
    lines: list[str] = []
    for para in strip_markup(text).split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if m.width_pt(trial, size) <= width_pt or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def text_height_in(text: str, family: str, size: float, width_in: float,
                   bold: bool = False, spacing: float = 1.0, para_gap_pt: float = 0) -> float:
    m = metrics(family, bold)
    lines = wrap(text, family, size, width_in, bold)
    paras = strip_markup(text).count("\n")
    return (len(lines) * m.line * size * spacing + paras * para_gap_pt) / 72


def fits(text: str, family: str, size: float, w: float, h: float, bold=False, spacing=1.0,
         para_gap_pt=0) -> tuple[bool, float]:
    """Return (fits, fill_ratio). Also fails if a single word is wider than the box."""
    m = metrics(family, bold)
    too_wide = any(m.width_pt(word, size) > w * 72 for word in strip_markup(text).split())
    hh = text_height_in(text, family, size, w, bold, spacing, para_gap_pt)
    return (hh <= h and not too_wide), hh / h if h else 99


def capacity_words(family: str, size: float, w: float, h: float, bold=False, spacing=1.0,
                   sample: str | None = None) -> int:
    """Largest number of lorem words that fit the box (measured, not guessed)."""
    words = (sample or LOREM).split()
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        ok, _ = fits(" ".join(words[:mid]), family, size, w, h, bold, spacing)
        if ok:
            lo = mid
        else:
            hi = mid - 1
    return lo


LOREM = ("Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut "
         "labore et dolore magna aliqua Ut enim ad minim veniam quis nostrud exercitation ullamco laboris "
         "nisi ut aliquip ex ea commodo consequat Duis aute irure dolor in reprehenderit in voluptate velit "
         "esse cillum dolore eu fugiat nulla pariatur Excepteur sint occaecat cupidatat non proident sunt in "
         "culpa qui officia deserunt mollit anim id est laborum ") * 4
