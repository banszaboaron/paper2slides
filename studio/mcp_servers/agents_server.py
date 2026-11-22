"""sdl-agents: the LLM agents (theme, design, tweak, placeholder content). Provider: Claude API or replay."""
from _common import FastMCP, Context, run, run_child, svc
from sdl import agents

mcp = FastMCP("sdl-agents", log_level="WARNING")


@mcp.tool()
async def llm_status(session: str = "", provider: str = "") -> dict:
    """Which LLM provider/model is configured (for a session: the one chosen on its first screen) and whether
    it is ready (API key present / Claude Code CLI found). provider: check another one instead."""
    return agents.provider_info(session or None, provider or None)


@mcp.tool()
async def read_document(session: str, index: int, ctx: Context = None) -> dict:
    """Claude reads a PDF that has no text layer (slides or scans as images) page by page (document #index,
    0 = the paper); the transcript becomes the document's text for every later agent."""
    return await run(ctx, agents.logged(agents.read_document), session, index, log=True)


@mcp.tool()
async def write_brief(session: str, ctx: Context = None) -> dict:
    """Theme agent: read the paper, write brief.json (mood, keywords, communication needs, metaphors)."""
    return await run(ctx, agents.logged(agents.write_brief), session, log=True)


@mcp.tool()
async def get_brief(session: str) -> dict:
    """The current brief.json (empty if missing)."""
    return svc.get_brief(session) or {}


@mcp.tool()
async def save_brief(session: str, brief: dict) -> dict:
    """Overwrite brief.json (after a human edit)."""
    return await run(None, svc.save_brief, session, brief)


@mcp.tool()
async def propose_options(session: str, stage: str, feedback: str = "", ctx: Context = None) -> dict:
    """Design agent: write 4 options for a ★ stage (style|brand|visual|motion), validated in a feedback
    loop. feedback = why the previous 4 were rejected ("egyik sem"), or an extra wish."""
    return await run(ctx, agents.logged(agents.propose_options), session, stage, feedback, log=True)


@mcp.tool()
async def tweak_option(session: str, stage: str, key: str, instruction: str, ctx: Context = None) -> dict:
    """Tweak agent: turn a free-text tweak of one option into a JSON patch (apply it with design.choose_option)."""
    return await run(ctx, agents.tweak_patch, session, stage, key, instruction)


@mcp.tool()
async def draft_placeholder_spec(session: str, ctx: Context = None) -> dict:
    """Content agent: the slide spec from the paper (with its figures) at the kick-off slide count and limits."""
    return await run(ctx, agents.logged(agents.draft_spec), session, log=True)


@mcp.tool()
async def review_deck(session: str, target: str = "deck", ctx: Context = None) -> dict:
    """Visual judge: Claude looks at the rendered slides of the final deck (target='deck') or of one motion
    option (target='motion:<key>') and reports problems per slide, with layout fixes where possible."""
    return await run(ctx, agents.logged(agents.review), session, target, log=True)


@mcp.tool()
async def refine_deck(session: str, requests: list[dict] | None = None, general: str = "", ctx: Context = None) -> dict:
    """Polish step: apply the humans' change requests (per slide: [{id, n, text}], and/or one general request).
    Only the requested slides change; the new version is saved (the previous one can be restored)."""
    import json
    f = svc.sdir(session) / "spec" / "_refine_request.json"           # the agent runs in its own process:
    f.write_text(json.dumps({"requests": requests or [], "general": general}, ensure_ascii=False), encoding="utf-8")
    return await run_child(ctx, "refine", session, "--requests", str(f))   # font measuring in a thread hung on Windows


if __name__ == "__main__":
    mcp.run()
