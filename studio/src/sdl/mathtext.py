"""Inline math for slide text.

Markup: anything between $...$ in a text slot is a small LaTeX subset, e.g.
    "SVD of $W_{Geo}$, keeps $\\rho = 20\\%$ of $|\\hat{W}_r|$"

It is rendered as native, editable PowerPoint runs (not images): single-letter
variables in italic, words and digits upright, real sub/superscripts (run
baseline offset), Greek letters and operators as Unicode, all in a math font.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MATH_FONT = "Cambria Math"   # ships with Windows/Office; covers all symbols below
INLINE = re.compile(r"\$([^$]+)\$")

SUB, SUP = -25000, 30000     # PowerPoint baseline offsets (1/1000 %)

SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "eta": "η", "theta": "θ",
    "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω",
    "Sigma": "Σ", "Delta": "Δ", "Omega": "Ω",
    "odot": "⊙", "otimes": "⊗", "oplus": "⊕", "cup": "∪", "cap": "∩", "cdot": "·", "times": "×",
    "approx": "≈", "sim": "∼", "neq": "≠", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "pm": "±",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "in": "∈", "infty": "∞", "sum": "∑", "top": "⊤",
    "nabla": "∇", "partial": "∂", "ell": "ℓ", "mid": "|", "%": "%", "$": "$",
    ",": " ", ";": " ", " ": " ", "quad": "  ",
}
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
SYMBOLS.update(GREEK)
SYMBOLS.update({
    "prod": "∏", "int": "∫", "forall": "∀", "exists": "∃", "neg": "¬", "lnot": "¬", "wedge": "∧", "land": "∧",
    "vee": "∨", "lor": "∨", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇", "notin": "∉",
    "emptyset": "∅", "varnothing": "∅", "propto": "∝", "equiv": "≡", "ll": "≪", "gg": "≫", "simeq": "≃",
    "cong": "≅", "mp": "∓", "div": "÷", "ast": "∗", "star": "⋆", "circ": "∘", "bullet": "•", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "Leftrightarrow": "⇔", "leftrightarrow": "↔", "mapsto": "↦", "implies": "⇒", "iff": "⇔",
    "uparrow": "↑", "downarrow": "↓", "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈",
    "rceil": "⌉", "|": "‖", "Vert": "‖", "vert": "|", "{": "{", "}": "}", "_": "_", "#": "#", "&": "&",
    "dots": "…", "ldots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱", "prime": "′", "dagger": "†",
    "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "perp": "⊥", "parallel": "∥", "angle": "∠",
    "triangle": "△", "square": "□", "checkmark": "✓", "colon": ":", "!": "", ":": " ", "qquad": "    ",
    "lvert": "|", "rvert": "|", "lVert": "‖", "rVert": "‖", "setminus": "∖", "smallsetminus": "∖",
})
FUNCS = {"log", "ln", "exp", "max", "min", "arg", "argmax", "argmin", "sup", "inf", "lim", "sin", "cos", "tan",
         "det", "dim", "ker", "Pr", "deg", "gcd", "mod", "sgn", "tr", "softmax", "KL"}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "𝔼", "P": "ℙ"}
IGNORED = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "displaystyle",
           "textstyle", "limits", "nolimits"}
STYLE_CMDS = {"mathbf", "boldsymbol", "bm", "mathit", "mathsf", "mathtt", "mathcal", "mathscr", "mathfrak", "textbf",
              "textit", "emph"}
UPRIGHT_CMDS = {"mathrm", "text", "operatorname", "textrm"}
ACCENTS = {"hat": "̂", "bar": "̄", "tilde": "̃", "vec": "⃗", "dot": "̇"}


UNKNOWN: list[str] = []          # commands met by the last parse that we cannot render


@dataclass
class Run:
    text: str
    italic: bool = False
    baseline: int = 0


def _group(s: str, i: int) -> tuple[str, int]:
    """Read a {...} group (or a single char/command) starting at s[i]."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", s[i:])
        return m.group(0), i + len(m.group(0))
    return s[i], i + 1


def _parse(s: str, base: int, upright: bool, out: list[Run]):
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[i:])
            cmd, i = m.group(1), i + len(m.group(0))
            if cmd in UPRIGHT_CMDS:
                g, i = _group(s, i)
                _parse(g, base, True, out)
            elif cmd in ACCENTS:
                g, i = _group(s, i)
                inner: list[Run] = []
                _parse(g, base, upright, inner)
                if inner:
                    last = inner[-1]
                    last.text = unicodedata.normalize("NFC", last.text + ACCENTS[cmd])
                out.extend(inner)
            elif cmd in IGNORED:
                continue
            elif cmd in ("frac", "dfrac", "tfrac"):
                a, i = _group(s, i)
                b, i = _group(s, i)
                wrap = lambda t: len(re.sub(r"\\[A-Za-z]+|[{}\s]", "x", t)) > 1  # noqa: E731
                if wrap(a):
                    out.append(Run("(", False, base))
                _parse(a, base, upright, out)
                if wrap(a):
                    out.append(Run(")", False, base))
                out.append(Run("/", False, base))
                if wrap(b):
                    out.append(Run("(", False, base))
                _parse(b, base, upright, out)
                if wrap(b):
                    out.append(Run(")", False, base))
            elif cmd == "sqrt":
                if i < len(s) and s[i] == "[":                 # \sqrt[n]{x}: the root index is dropped
                    i = s.index("]", i) + 1 if "]" in s[i:] else i
                g, i = _group(s, i)
                out.append(Run("√", False, base))
                long = len(re.sub(r"\\[A-Za-z]+", "x", g).replace("{", "").replace("}", "")) > 1
                if long:
                    out.append(Run("(", False, base))
                _parse(g, base, upright, out)
                if long:
                    out.append(Run(")", False, base))
            elif cmd == "mathbb":
                g, i = _group(s, i)
                out.append(Run("".join(BLACKBOARD.get(ch, ch) for ch in g), False, base))
            elif cmd in STYLE_CMDS:
                g, i = _group(s, i)
                _parse(g, base, upright, out)
            elif cmd in FUNCS:
                out.append(Run(cmd, False, base))
            elif cmd in SYMBOLS:
                out.append(Run(SYMBOLS[cmd], False, base))
            else:
                UNKNOWN.append(cmd)
                out.append(Run(cmd, False, base))
        elif c in "_^":
            g, i = _group(s, i + 1)
            word = re.fullmatch(r"[A-Za-z]{2,}", g) is not None   # W_{Geo}: 'Geo' is a label, upright
            _parse(g, SUB if c == "_" else SUP, upright or word, out)
        elif c in "{}":
            i += 1
        elif c.isalpha():
            out.append(Run(c, not upright, base))
            i += 1
        elif c == "-":
            out.append(Run("−", False, base))
            i += 1
        else:
            out.append(Run(c, False, base))
            i += 1


def parse(src: str) -> list[Run]:
    raw: list[Run] = []
    _parse(src, 0, False, raw)
    merged: list[Run] = []
    for r in raw:
        if merged and merged[-1].italic == r.italic and merged[-1].baseline == r.baseline:
            merged[-1].text += r.text
        else:
            merged.append(Run(r.text, r.italic, r.baseline))
    return merged


def plain(src: str) -> str:
    """Display string of a math snippet (for measuring and word counts)."""
    return "".join(r.text for r in parse(src))


def strip_inline(text: str) -> str:
    return INLINE.sub(lambda m: plain(m.group(1)), text)


def typography(text: str) -> str:
    """Fix ASCII stand-ins in plain (non-math) text: '~0.4' -> '≈ 0.4'."""
    text = re.sub(r"(?<![\w~])~\s?(?=[\d.])", "≈ ", text)
    return text


def unknown_commands(text: str) -> list[str]:
    """LaTeX commands inside $...$ in `text` that the inline renderer does not know."""
    found: list[str] = []
    for m in INLINE.finditer(text or ""):
        UNKNOWN.clear()
        parse(m.group(1))
        found += UNKNOWN
    UNKNOWN.clear()
    return sorted(set(found))
