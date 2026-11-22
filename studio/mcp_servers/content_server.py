"""sdl-content: paper intake, kick-off limits and the slide spec (the hand-over point from the content team)."""
from _common import FastMCP, Context, run, run_child, svc

mcp = FastMCP("sdl-content", log_level="WARNING")


@mcp.tool()
async def list_sessions() -> list[dict]:
    """All sessions in sessions/ with paper title and next step."""
    return svc.list_sessions()


@mcp.tool()
async def session_status(session: str) -> dict:
    """Steps done / next step, choices, spec and deck info of one session."""
    return await run(None, svc.status, session)


@mcp.tool()
async def ingest_paper(session: str, pdf_path: str, ctx: Context = None) -> dict:
    """Copy the paper PDF into the session and extract its text (paper/text.txt)."""
    return await run(ctx, svc.ingest_paper, session, pdf_path)


@mcp.tool()
async def add_source(session: str, pdf_path: str, role: str = "own_results", comment: str = "",
                     ctx: Context = None) -> dict:
    """Add another document next to the paper (e.g. the team's replication report).
    role: own_results | notes | other. comment: how the talk should use this document."""
    return await run(ctx, svc.add_source, session, pdf_path, role, comment)


@mcp.tool()
async def update_source(session: str, index: int, comment: str | None = None, role: str | None = None) -> dict:
    """Change the comment (or role) of document #index (0 = the paper)."""
    return await run(None, svc.update_source, session, index, comment, role)


@mcp.tool()
async def remove_source(session: str, index: int) -> dict:
    """Remove an extra document (not the paper)."""
    return await run(None, svc.remove_source, session, index)


@mcp.tool()
async def set_team(session: str, team: list[str], venue: str = "", closing_title: str = "Thank you",
                   closing_subtitle: str = "Questions?") -> dict:
    """The presenters (title slide), the venue line, and the words of the last slide."""
    return await run(None, svc.set_team, session, team, venue, closing_title, closing_subtitle)


@mcp.tool()
async def set_llm(session: str, provider: str, model: str = "sonnet") -> dict:
    """Which model runs every agent of this session. provider: anthropic (API key) | claude_cli (Claude Code
    CLI, subscription); model: sonnet | opus | haiku."""
    return await run(None, svc.set_llm, session, provider, model)


@mcp.tool()
async def get_paper_text(session: str, max_chars: int = 20000) -> str:
    """The extracted paper text (truncated)."""
    return (await run(None, svc.paper_text, session))[:max_chars]


@mcp.tool()
async def kickoff(session: str, slides: int, density: str = "medium", max_chars: int | None = None,
                  minutes: int = 20, notes: str = "", direction: str = "") -> dict:
    """Record the kick-off answers. density: tight|medium|dense (or max_chars for a plain content slide).
    direction: academic|bold|tech|minimal. Writes limits.json (max visible characters per slide type)."""
    return await run(None, svc.kickoff, session, slides, minutes, density, max_chars, notes, direction)


@mcp.tool()
async def get_limits(session: str) -> dict:
    """Per slide type character limits set at kick-off."""
    st = svc.load_state(session)
    if "kickoff" not in st:
        raise ValueError("no kick-off yet")
    return st["kickoff"]["char_limits"]


@mcp.tool()
async def import_spec(session: str, spec_path: str, placeholder: bool = False) -> dict:
    """Import and check a logical slide spec (deck JSON + images). Rejected on any error
    (over the character limit, unknown type, missing image or meaning, > 3 builds)."""
    # in a child process: measuring the fit with the chosen fonts hung in a worker thread on Windows
    return await run_child(None, "spec", session, "--file", spec_path, *(["--placeholder"] if placeholder else []))


@mcp.tool()
async def get_spec(session: str) -> dict:
    """The imported slide spec."""
    import json
    return json.loads(svc.spec_path(session).read_text(encoding="utf-8"))


if __name__ == "__main__":
    mcp.run()
