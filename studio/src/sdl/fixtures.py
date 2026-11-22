"""Preview content (brief decision #1): lorem text, but generic AI placeholders for
figure / formula / chart (attention formula, pipeline diagram, simple bar chart)."""
from __future__ import annotations

from pathlib import Path

from .brand import Brand

LOREM_TITLE = "Lorem ipsum dolor sit amet"
L = ["Lorem ipsum dolor sit amet, consectetur adipiscing elit",
     "Sed do eiusmod tempor incididunt ut labore et dolore",
     "Ut enim ad minim veniam, quis nostrud exercitation",
     "Duis aute irure dolor in reprehenderit in voluptate"]
ATTENTION = r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\left(\frac{QK^{\top}}{\sqrt{d_k}}\right)V"


def generic_figure(b: Brand, out: Path) -> Path:
    """A generic transformer-block figure (placeholder, no paper content)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5), dpi=200)
    ax.set_xlim(0, 7)
    ax.set_ylim(0, 5)
    ax.axis("off")
    fig.patch.set_alpha(0)
    boxes = [("Embedding", 0.4, b.baseline), ("Multi-head attention", 1.45, b.ours), ("Add & Norm", 2.5, b.baseline),
             ("Feed-forward", 3.55, b.accent2)]
    for name, y, col in boxes:
        ax.add_patch(FancyBboxPatch((1.5, y), 4, 0.75, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc="#" + col, ec="none", alpha=0.9))
        ax.text(3.5, y + 0.37, name, ha="center", va="center", color="white", fontsize=15, family="Inter")
    for y in (1.15, 2.2, 3.25):
        ax.annotate("", xy=(3.5, y + 0.3), xytext=(3.5, y), arrowprops=dict(arrowstyle="-|>", color="#" + b.muted, lw=1.6))
    fig.savefig(out, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return out


def preview_deck(b: Brand, asset_dir: Path, full: bool = True) -> dict:
    fig = str(generic_figure(b, asset_dir / f"generic_figure_{b.key}.png"))
    slides = [
        {"id": "title", "type": "title", "section": "", "content": {
            "title": LOREM_TITLE, "subtitle": "Consectetur adipiscing elit sed do eiusmod",
            "authors": "Lorem Ipsum · Dolor Sit · Amet Consectetur"}},
        {"id": "diagram", "type": "diagram", "section": "Lorem", "content": {
            "title": "Lorem ipsum: dolor sit amet", "steps": [
                "Tokenizer", "Encoder", {"text": "Attention", "role": "ours", "name": "!!hero"}, "Decoder"],
            "caption": "Lorem ipsum dolor sit amet, consectetur adipiscing elit.", "builds": {2: 1, 3: 2}}},
        {"id": "formula", "type": "formula", "section": "Lorem", "content": {
            "title": "Lorem ipsum dolor", "latex": ATTENTION,
            "terms": [("Q,K", "lorem ipsum dolor"), (r"\sqrt{d_k}", "sit amet elit"), ("V", "sed do eiusmod")]}},
        {"id": "chart", "type": "evidence", "section": "Ipsum", "variant": "evidence_chart_side", "content": {
            "title": "Lorem ipsum dolor sit amet elit", "chart": {
                "type": "bar", "categories": ["Base", "LoRA", "PiSSA", "Ours"],
                "series": [{"name": "acc", "values": [51.0, 58.2, 60.5, 63.9]}],
                "point_roles": ["baseline", "baseline", "baseline", "ours"], "y_min": 40, "fmt": "0.0"},
            "takeaway": "Lorem ipsum ==dolor sit amet==, consectetur adipiscing elit sed do."}},
    ]
    if full:
        slides += [
            {"id": "section", "type": "section", "section": "Ipsum", "content": {"number": "02", "title": "Dolor sit amet"}},
            {"id": "figtext", "type": "figure_text", "section": "Ipsum", "content": {
                "title": "Lorem ipsum dolor sit", "image": fig, "meaning": "placeholder: generic transformer block",
                "body": L[:3]},
             "zoom": [{"region": (0.18, 0.5, 0.82, 0.76), "label": "ZOOM · ATTENTION",
                       "text": "Lorem ipsum ==dolor sit amet==, consectetur adipiscing elit."}]},
            {"id": "compare", "type": "comparison", "section": "Ipsum", "variant": "comparison_two", "content": {
                "title": "Lorem ipsum vs. dolor sit", "columns": [
                    {"head": "Lorem", "items": L[:2]}, {"head": "Ipsum", "items": L[2:], "role": "ours"}]}},
            {"id": "statement", "type": "statement", "section": "Ipsum", "variant": "statement_number", "content": {
                "number": "99.5%", "text": "Lorem ipsum dolor sit amet, consectetur adipiscing."}},
            {"id": "closing", "type": "closing", "section": "Dolor", "content": {
                "message": "Lorem ipsum dolor sit amet.", "takeaways": [L[0], L[1], L[2]]}},
        ]
    return {"title": "preview", "slides": slides}
