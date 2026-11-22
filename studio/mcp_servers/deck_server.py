"""sdl-deck: assemble the full deck from the spec with the chosen tokens, plus its check report."""
from _common import FastMCP, Context, run, run_child, svc

mcp = FastMCP("sdl-deck", log_level="WARNING")


@mcp.tool()
async def build_deck(session: str, name: str = "", video: bool = False, ctx: Context = None) -> dict:
    """Full deck -> sessions/<s>/deck/<name>.pptx + contact sheet + check report (video=True: also its video)."""
    return await run_child(ctx, "build", session, *(["--name", name] if name else []), *(["--video"] if video else []))


@mcp.tool()
async def deck_report(session: str) -> dict:
    """The last build's files and error count."""
    st = svc.load_state(session)
    if "deck" not in st:
        raise ValueError("no deck built yet")
    return st["deck"]


@mcp.tool()
async def apply_review_fixes(session: str, target: str = "deck", ctx: Context = None) -> dict:
    """Accept the judge's layout fixes (spec/overrides.json) and rebuild the deck."""
    # in a child process: checking that a fix keeps every word lays slides out (hung in a worker thread on Windows)
    fx = await run_child(None, "fixes", session, "--target", target)
    built = await run_child(ctx, "build", session)
    return {"applied": fx["applied"], "rejected": fx.get("rejected", []), "deck": built}


@mcp.tool()
async def refine_undo(session: str) -> dict:
    """Polish step: back to the version before the last change round (rebuild the deck afterwards)."""
    return await run(None, svc.refine_undo, session)


@mcp.tool()
async def refine_approve(session: str, ok: bool = True) -> dict:
    """Polish step: the humans are happy with this version (ok=False reopens the polishing)."""
    return await run(None, svc.refine_approve, session, ok)


if __name__ == "__main__":
    mcp.run()
