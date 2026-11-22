"""sdl-office: PowerPoint on the laptop – font install and final export (PNG/PDF/MP4 + COM report)."""
from _common import FastMCP, Context, run, svc
from sdl import office, fontfetch

mcp = FastMCP("sdl-office", log_level="WARNING")


@mcp.tool()
async def office_status() -> dict:
    """Whether PowerPoint COM is usable here."""
    return office.available()


@mcp.tool()
async def install_fonts() -> dict:
    """Install every font in fonts/ for the current user (Windows). Restart PowerPoint if a deck is open."""
    await run(None, fontfetch.install_for_preview)
    return {"ok": True}


@mcp.tool()
async def export_deck(session: str, png: bool = True, pdf: bool = True, mp4: bool = False,
                      ctx: Context = None) -> dict:
    """Export the session's built deck with PowerPoint (real Morph/builds in the MP4)."""
    st = svc.load_state(session)
    if "deck" not in st:
        raise ValueError("build the deck first")
    pptx = str(svc.ROOT / st["deck"]["pptx"])
    rep = await run(ctx, office.export, pptx, png, pdf, mp4)
    st["export"] = {"out_dir": svc.rel(rep["out_dir"]), "files": [svc.rel(f) for f in rep["files"]],
                    "fonts_not_installed": rep["fonts_not_installed"]}
    svc.save_state(session, st)
    return st["export"] | {"transitions": rep["transitions"]}


@mcp.tool()
async def open_deck(session: str, what: str = "pptx") -> dict:
    """Open the built deck in PowerPoint (what=pptx) or its folder in Explorer (what=folder)."""
    st = svc.load_state(session)
    if "deck" not in st:
        raise ValueError("build the deck first")
    p = svc.ROOT / st["deck"]["pptx"]
    return await run(None, office.open_file, str(p if what == "pptx" else p.parent))


if __name__ == "__main__":
    mcp.run()
