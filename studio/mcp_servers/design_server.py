"""sdl-design: the six ★ stages (palette, type, graphics, visual, layout, motion) – options, validation, previews, choice."""
from _common import FastMCP, Context, run, run_child, svc
from sdl import preview

mcp = FastMCP("sdl-design", log_level="WARNING")


@mcp.tool()
async def renderer() -> dict:
    """Which renderer the previews use (powerpoint on the laptop, libreoffice in the cloud)."""
    try:
        return {"renderer": preview.backend()}
    except RuntimeError as e:
        return {"renderer": None, "error": str(e)}


@mcp.tool()
async def get_options(session: str, stage: str) -> dict:
    """options/<stage>.json (empty if not written yet)."""
    return svc.get_options(session, stage) or {}


@mcp.tool()
async def save_options(session: str, stage: str, options: dict) -> dict:
    """Write options/<stage>.json ({"options": [4 items]}) and validate it."""
    return await run(None, svc.save_options, session, stage, options)


@mcp.tool()
async def validate_options(session: str, stage: str) -> dict:
    """Readability, real Google Fonts, diversity of the 4 options."""
    return await run(None, svc.validate_options, session, stage)


@mcp.tool()
async def render_options(session: str, stage: str, ctx: Context = None) -> dict:
    """Prepare the 4 options for the board. palette/type/graphics/visual: resolved tokens + font files (drawn
    in the browser). layout: lorem-ipsum slides rendered by PowerPoint. motion: the team's real deck per
    option + PowerPoint video (GIF simulation without PowerPoint)."""
    return await run_child(ctx, "render", session, stage)


@mcp.tool()
async def choose_option(session: str, stage: str, key: str, patch: dict | None = None, by: str = "human") -> dict:
    """Record the human's choice (optionally with a patch). A new main style resets the later stages.
    by: "human" (the UI) or how a script chose ("auto: first option", "scripted"), shown on the workflow slide."""
    return await run(None, svc.choose, session, stage, key, patch, by)


if __name__ == "__main__":
    mcp.run()
