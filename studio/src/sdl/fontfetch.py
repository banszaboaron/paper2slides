"""Fetch any Google Fonts family on demand (from the google/fonts GitHub repo).

Variable fonts are cut into static Regular / SemiBold / Bold instances, so that
PowerPoint (which needs installed static fonts) and the text measurer agree.
Every installed family is recorded in fonts/registry.json, which fonts.py reads.

    from sdl.fontfetch import ensure_family
    ensure_family("IBM Plex Sans")   # -> {"IBM Plex Sans": ..., "IBM Plex Sans SemiBold": ...}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"
REGISTRY = FONT_DIR / "registry.json"
RAW = "https://raw.githubusercontent.com/google/fonts/main"
LICENSE_DIRS = ("ofl", "apache", "ufl")
WEIGHTS = {"Regular": 400, "SemiBold": 600, "Bold": 700}


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except Exception:
        return None


def slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]", "", family.lower())


def metadata(family: str) -> tuple[str, str] | None:
    """(license_dir, METADATA.pb text) or None if the family is not on Google Fonts."""
    for lic in LICENSE_DIRS:
        txt = _get(f"{RAW}/{lic}/{slug(family)}/METADATA.pb")
        if txt:
            return lic, txt.decode("utf-8")
    return None


_CAT: dict[str, str | None] = {}


def category(family: str) -> str | None:
    if family in _CAT:
        return _CAT[family]
    _CAT[family] = _category(family)
    return _CAT[family]


def _category(family: str) -> str | None:
    md = metadata(family)
    if not md:
        return None
    m = re.search(r'category:\s*"([A-Z_]+)"', md[1])
    return m.group(1) if m else None


def load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {}


def save_registry(reg: dict):
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")


def _static_instance(var_path: Path, weight: int, out: Path, family: str, style: str) -> bool:
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    f = TTFont(str(var_path))
    axes = {a.axisTag: (a.minValue, a.maxValue) for a in f["fvar"].axes}
    if "wght" in axes and not (axes["wght"][0] <= weight <= axes["wght"][1]):
        return False
    loc = {tag: (weight if tag == "wght" else None) for tag in axes}
    loc = {k: v for k, v in loc.items() if v is not None}
    for tag, (lo, hi) in axes.items():          # pin every other axis to its default
        if tag != "wght":
            loc[tag] = next(a.defaultValue for a in f["fvar"].axes if a.axisTag == tag)
    inst = instancer.instantiateVariableFont(f, loc)
    _rename(inst, family, style)
    inst.save(str(out))
    return True


def _rename(font, family: str, style: str):
    """Legacy (RIBBI) naming: 'Family SemiBold' / Regular, so Office lists it as its own family."""
    name = font["name"]
    fam = family if style in ("Regular", "Bold") else f"{family} {style}"
    sub = "Bold" if style == "Bold" else "Regular"
    full = f"{family} {style}"
    ps = re.sub(r"[^A-Za-z0-9-]", "", f"{family.replace(' ', '')}-{style}")
    for rec in list(name.names):
        if rec.nameID in (16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    for nid, val in ((1, fam), (2, sub), (3, f"{ps};sdl"), (4, full), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    os2 = font["OS/2"]
    os2.usWeightClass = WEIGHTS[style]
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | (0b100000 if style == "Bold" else 0b1000000)
    font["head"].macStyle = 1 if style == "Bold" else 0


def ensure_family(family: str, refresh_system: bool = True) -> dict:
    """Download + register a Google Fonts family. Returns the registry entries for it."""
    reg = load_registry()
    if family in reg and all((FONT_DIR / f).exists() for f in reg[family].values()):
        return {k: v for k, v in reg.items() if k == family or k.startswith(family + " ")}
    md = metadata(family)
    if not md:
        raise ValueError(f"'{family}' is not on Google Fonts (check the exact family name)")
    lic, txt = md
    files = re.findall(r'filename:\s*"([^"]+)"', txt)
    styles = re.findall(r'style:\s*"([^"]+)"', txt)
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FONT_DIR / "_src"
    tmp.mkdir(exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9]", "", family)
    out = {}
    upright = [fn for fn, st in zip(files, styles) if st == "normal"] or files
    var = [fn for fn in upright if "[" in fn]
    if var:
        src = tmp / var[0]
        if not src.exists():
            data = _get(f"{RAW}/{lic}/{slug(family)}/{urllib.request.quote(var[0])}")
            if not data:
                raise RuntimeError(f"download failed: {var[0]}")
            src.write_bytes(data)
        for style, w in WEIGHTS.items():
            dst = FONT_DIR / f"{base}-{style}.ttf"
            if dst.exists() or _static_instance(src, w, dst, family, style):
                out[style] = dst.name
    else:                                         # static family: pick the closest weights
        weights = [int(x) for x in re.findall(r"weight:\s*(\d+)", txt)]
        cands = [(fn, w) for fn, st, w in zip(files, styles, weights) if st == "normal"]
        for style, w in WEIGHTS.items():
            fn, _ = min(cands, key=lambda c: abs(c[1] - w))
            dst = FONT_DIR / f"{base}-{style}.ttf"
            if not dst.exists():
                data = _get(f"{RAW}/{lic}/{slug(family)}/{fn}")
                if not data:
                    continue
                dst.write_bytes(data)
                from fontTools.ttLib import TTFont
                f = TTFont(str(dst))
                _rename(f, family, style)
                f.save(str(dst))
            out[style] = dst.name
    if "Regular" not in out:
        raise RuntimeError(f"no usable regular weight for {family}")
    reg[family] = {"regular": out["Regular"], "bold": out.get("Bold", out["Regular"])}
    if "SemiBold" in out:
        reg[f"{family} SemiBold"] = {"regular": out["SemiBold"], "bold": out.get("Bold", out["SemiBold"])}
    save_registry(reg)
    if refresh_system:
        install_for_preview()
    return {k: v for k, v in reg.items() if k == family or k.startswith(family + " ")}


def install_for_preview():
    """Make the fonts visible to the renderer.

    Linux (cloud): copy into ~/.local/share/fonts + fc-cache (LibreOffice).
    Windows (laptop): run tools/win/install_fonts.ps1 (per-user install, no admin) whenever the
    set of font files changed since the last run. PowerPoint only sees new fonts after a restart;
    the PowerPoint renderer quits its own instance, so this matters only if a deck is open.
    """
    try:
        if os.name == "nt":
            files = sorted(f.name for f in FONT_DIR.glob("*.ttf"))
            stamp = FONT_DIR / ".installed"
            if stamp.exists() and stamp.read_text(encoding="utf-8").split("\n") == files:
                return
            ps1 = FONT_DIR.parent / "tools" / "win" / "install_fonts.ps1"
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            stamp.write_text("\n".join(files), encoding="utf-8")
            return
        target = Path.home() / ".local" / "share" / "fonts" / "sdl"
        target.mkdir(parents=True, exist_ok=True)
        for f in FONT_DIR.glob("*.ttf"):
            if not (target / f.name).exists():
                shutil.copy(f, target / f.name)
        if shutil.which("fc-cache"):
            subprocess.run(["fc-cache", "-f", str(target)], capture_output=True, timeout=120)
    except Exception:
        pass
