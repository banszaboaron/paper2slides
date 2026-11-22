"""Shared bootstrap for the slide-design-lab MCP servers (stdio).

Every server is small and owns one part of the pipeline; the web app (webapp/server.py)
and later the team's LangGraph/OpenClaw harness talk to them over MCP.
"""
import functools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import anyio                                      # noqa: E402
from mcp.server.fastmcp import FastMCP, Context   # noqa: E402,F401

from sdl import service as svc                    # noqa: E402


async def run(ctx: Context | None, fn, *args, **kwargs):
    """Run a blocking pipeline function in a worker thread.

    StepError -> readable tool error. If `log=True` is passed, fn receives a callback that
    forwards progress lines to the MCP client as log messages (ctx.info)."""
    if kwargs.pop("log", False):
        import asyncio
        loop = asyncio.get_running_loop()

        def log(msg: str):                          # callable from any thread (also the agents' worker pools)
            if ctx is not None:
                try:
                    asyncio.run_coroutine_threadsafe(ctx.info(msg), loop)
                except Exception:
                    pass
        kwargs["log"] = log
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except svc.StepError as e:
        raise ValueError(str(e)) from None
    except Exception as e:                          # say where it happened: the message alone is often useless
        import traceback
        tb = traceback.extract_tb(e.__traceback__)
        ours = [f for f in tb if "-packages" not in f.filename and "anyio" not in f.filename] or tb
        where = " ← ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(ours[-3:]))
        last = tb[-1]
        lib = f" (inside {Path(last.filename).name}:{last.lineno})" if last not in ours else ""
        traceback.print_exc()
        raise ValueError(f"{type(e).__name__}: {e} [at {where}{lib}]") from None


def run_cli(ctx: Context | None, *args: str) -> dict:
    """Run a heavy step (render, build) in a child process: `python tools/workflow.py <args>`.

    Same path as the CLI, so PowerPoint COM gets its own process and main thread (COM in a
    worker thread of the MCP server hung on Windows). stdin is closed and stdout is piped –
    the server's own stdin/stdout carry the MCP protocol. Progress lines go to ctx.info."""
    import json
    import os
    import subprocess
    import tempfile

    def log(msg: str):
        if ctx is not None:
            try:
                anyio.from_thread.run(ctx.info, msg)
            except Exception:
                pass

    with tempfile.TemporaryDirectory() as td:
        res = Path(td) / "result.json"
        env = dict(os.environ, SDL_RESULT=str(res), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "workflow.py"), *args], cwd=str(ROOT), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail = []
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail = (tail + [line])[-15:]
            if line.startswith("  .. "):
                log(line[5:])
        p.wait()
        if p.returncode != 0 or not res.exists():
            raise ValueError("\n".join(tail) or f"workflow.py {' '.join(args)} failed ({p.returncode})")
        return json.loads(res.read_text(encoding="utf-8"))


async def run_child(ctx: Context | None, *args: str) -> dict:
    return await anyio.to_thread.run_sync(lambda: run_cli(ctx, *args))
