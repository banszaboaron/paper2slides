"""Local selection UI for the design pipeline.

    python webapp/server.py            ->  http://127.0.0.1:8765

The web app is an MCP host: it starts the slide-design-lab MCP servers (mcp_servers/*) over stdio
and only talks to them through MCP tool calls – exactly what the team's LangGraph/OpenClaw harness
will do later. Long tool calls run as background jobs that the page polls.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path

import uvicorn

if sys.platform == "win32":                 # silence the harmless WinError 10054 when the browser drops a socket
    from asyncio import proactor_events as _pe
    _orig_lost = _pe._ProactorBasePipeTransport._call_connection_lost

    def _quiet_lost(self, exc):
        try:
            _orig_lost(self, exc)
        except (ConnectionResetError, ConnectionAbortedError):
            pass
    _pe._ProactorBasePipeTransport._call_connection_lost = _quiet_lost
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
SERVERS = ["content", "agents", "design", "deck", "office", "team"]
LONG = {"write_brief", "propose_options", "tweak_option", "draft_placeholder_spec", "render_options",
        "build_deck", "export_deck", "ingest_paper", "validate_options", "save_options", "choose_option",
        "import_spec", "install_fonts", "review_deck", "apply_review_fixes", "add_source", "team_start", "read_document", "team_reimport", "team_continue",
        "team_resume", "refine_deck"}
TIMEOUT = timedelta(minutes=40)


class Hub:
    """One MCP client session per server + background jobs."""

    def __init__(self):
        self.stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, list] = {}
        self.jobs: dict[str, dict] = {}
        self.active: dict[str, str] = {}          # server -> job id receiving its log lines

    async def start(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        for name in SERVERS:
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_servers" / f"{name}_server.py")],
                                           cwd=str(ROOT / "mcp_servers"), env=env)
            try:
                read, write = await self.stack.enter_async_context(stdio_client(params))
                sess = await self.stack.enter_async_context(
                    ClientSession(read, write, read_timeout_seconds=TIMEOUT, logging_callback=self._logger(name)))
                await sess.initialize()
                self.sessions[name] = sess
                self.tools[name] = [{"name": t.name, "description": t.description, "schema": t.inputSchema}
                                    for t in (await sess.list_tools()).tools]
                print(f"[mcp] {name}: {len(self.tools[name])} tools", flush=True)
            except Exception as e:                # one broken server must not stop the UI
                print(f"[mcp] {name} failed to start: {e}", flush=True)

    async def stop(self):
        await self.stack.aclose()

    def _logger(self, server):
        async def cb(params):
            jid = self.active.get(server)
            if jid in self.jobs:
                self.jobs[jid]["log"].append(str(params.data))
        return cb

    async def call(self, server: str, tool: str, args: dict, job: str | None = None):
        if server not in self.sessions:
            raise RuntimeError(f"MCP server '{server}' is not running")
        if job:
            self.active[server] = job
        r = await self.sessions[server].call_tool(tool, args, read_timeout_seconds=TIMEOUT)
        texts = [c.text for c in r.content if getattr(c, "type", "") == "text"]
        text = "\n".join(texts)
        if r.isError:
            raise RuntimeError(text or "tool error")
        if r.structuredContent is not None:
            sc = r.structuredContent
            return sc["result"] if set(sc) == {"result"} else sc
        try:
            vals = [json.loads(t) for t in texts]
            return vals[0] if len(vals) == 1 else vals
        except (json.JSONDecodeError, TypeError):
            return text

    def submit(self, server: str, tool: str, args: dict) -> str:
        for j in self.jobs.values():                  # the same work is already running (e.g. after F5): join it
            if j["state"] == "running" and j["server"] == server and j["tool"] == tool and j["args"] == args:
                return j["id"]
        jid = uuid.uuid4().hex[:10]
        self.jobs[jid] = {"id": jid, "server": server, "tool": tool, "args": args, "state": "running",
                          "started": time.time(), "log": [], "result": None, "error": None}
        if not hasattr(self, "locks"):
            self.locks = {}
        lock = self.locks.setdefault(args.get("session") or "", asyncio.Lock())

        async def run():
            j = self.jobs[jid]
            try:
                async with lock:                      # one long job per session at a time: no clobbered state
                    j["result"] = await self.call(server, tool, args, job=jid)
                j["state"] = "done"
            except Exception as e:
                j["error"], j["state"] = str(e), "error"
            j["ended"] = time.time()
        asyncio.get_running_loop().create_task(run())
        return jid


hub = Hub()


async def api_call(req: Request):
    body = await req.json()
    server, tool, args = body["server"], body["tool"], body.get("args") or {}
    if tool in LONG or body.get("background"):
        return JSONResponse({"job": hub.submit(server, tool, args)})
    try:
        return JSONResponse({"result": await hub.call(server, tool, args)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def api_job(req: Request):
    j = hub.jobs.get(req.path_params["jid"])
    if not j:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return JSONResponse(j | {"elapsed": round(j.get("ended", time.time()) - j["started"], 1)})


async def api_servers(_):
    return JSONResponse({k: [t["name"] for t in v] for k, v in hub.tools.items()} |
                        {"_missing": [s for s in SERVERS if s not in hub.sessions]})


async def api_upload(req: Request):
    name = Path(req.query_params.get("name", "upload.bin")).name
    sub = Path(req.query_params.get("dir", "uploads")).name
    dst = ROOT / "inbox" / sub / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(await req.body())
    return JSONResponse({"path": str(dst)})


async def api_inbox(_):
    inbox = ROOT / "inbox"
    specs = sorted(str(p) for p in inbox.glob("**/*.json")) if inbox.exists() else []
    pdfs = sorted(str(p) for p in inbox.glob("**/*.pdf")) if inbox.exists() else []
    return JSONResponse({"specs": specs, "pdfs": pdfs})


async def files(req: Request):
    p = (ROOT / req.path_params["path"]).resolve()
    if ROOT not in p.parents or not p.is_file():
        return Response("not found", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


async def static(req: Request):
    name = req.path_params.get("name") or "index.html"
    p = (STATIC / name).resolve()
    if STATIC not in p.parents or not p.is_file():
        return Response("not found", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@asynccontextmanager
async def lifespan(_):
    loop = asyncio.get_running_loop()
    default = loop.get_exception_handler()

    def quiet(lp, ctx):                     # Windows: the browser dropping a socket is not an error
        if isinstance(ctx.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
            return
        if default:
            default(lp, ctx)
        else:
            lp.default_exception_handler(ctx)
    loop.set_exception_handler(quiet)
    await hub.start()
    if not os.environ.get("SDL_NO_BROWSER"):
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{os.environ.get('SDL_PORT', '8765')}")
    yield
    try:                                    # MCP stdio children can hang on Windows: don't wait forever
        await asyncio.wait_for(hub.stop(), timeout=3)
    except BaseException:
        pass


app = Starlette(lifespan=lifespan, routes=[
    Route("/", static), Route("/static/{name}", static),
    Route("/api/call", api_call, methods=["POST"]), Route("/api/jobs/{jid}", api_job),
    Route("/api/servers", api_servers), Route("/api/upload", api_upload, methods=["POST"]),
    Route("/api/inbox", api_inbox), Route("/files/{path:path}", files),
])

if __name__ == "__main__":
    port = int(os.environ.get("SDL_PORT", "8765"))
    print(f"slide-design-lab UI: http://127.0.0.1:{port}", flush=True)
    print("Stop: Ctrl+C (or close this window)", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2)
    except KeyboardInterrupt:
        pass
    print("stopped.", flush=True)
    os._exit(0)                             # also ends any MCP child process still attached
