# Slide Studio – local selection UI

Start from the repository root:

```
.\start.bat               -> http://127.0.0.1:8765  (opens by itself)
```

Try it without a model: `.\start.bat demo`, then drop `examples\papers\minerva_2206.14858.pdf`
(the AI answers come from `studio\replay\minerva\`; at the content step import `examples\minerva\deck.json`).

## Flow

Sources (drop the paper, add more documents – e.g. the team's replication report – each with a comment on
how to use it; the presenters' names, venue, who presents what) → Theme (theme agent) → Kick-off (slide count, text density with a live sample,
4 direction cards) → ★ Palette → ★ Type → ★ Graphics → ★ Shapes → ★ Main style (placeholder slides to flip
through) → Content (the team's pipeline: outline review → slides → content review → presenter script; or a quick
one-call draft, or a deck.json) → ★ Deck look (4 near-final decks with PowerPoint video) → Final.

Every ★ step: the design agent writes 4 options; the card lights up under the pointer and a click chooses it;
"Regenerate" asks for 4 new ones. Every choice is kept in `session.json`; later previews and the final deck are
always composed from all of them.

Deck look: three classic decks that differ in ground (gradient, glow, grain, dot/line grid, contour map, aurora,
paper), navigation, section dividers and motion, plus one **canvas world** – a Prezi-like talk where every slide
is a camera on one big picture (road, mind map, mountain climb, neural network, orbit), chosen for the paper by
the motion director. The final deck is checked by Claude looking at the rendered slides; layout fixes are applied
silently.

## Architecture

```
browser ── /api/call ──> webapp/server.py (MCP host) ──stdio──> mcp_servers/
   content_server.py  paper, kick-off, slide spec
   agents_server.py   theme, design, motion director, visual judge, placeholder content (Claude API)
   design_server.py   ★ options: validation, previews, choice (renders in a child process)
   deck_server.py     final deck, review fixes
   office_server.py   PowerPoint COM: fonts, PNG/PDF/MP4 export, open
   team_server.py     the team's content pipeline (GeoRAProject/p2s, LangGraph): start, reviews, script
```

Shared logic: `src/sdl/service.py` + `src/sdl/stages.py` (the CLI `tools/workflow.py` uses the same).
Canvas world: `src/sdl/canvas.py`; slide grounds: `src/sdl/grounds.py`.
