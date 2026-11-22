"""LLM agents of the design pipeline (stand-ins until the team's harness takes over).

- theme agent      paper text            -> brief.json
- design agent     brief + stage + base  -> options/<stage>.json (4 options), validator feedback loop
- tweak agent      free-text tweak       -> JSON patch for one option
- content agent    paper + limits        -> placeholder slide spec (only if the team's spec is missing)

Provider (env / config.json, or per session: the model chosen on the Studio's first screen):
    SDL_LLM=anthropic (default) | claude_cli (the logged-in Claude Code CLI, subscription) | replay
    ANTHROPIC_API_KEY=...        SDL_MODEL=claude-sonnet-5-5 (default)
    SDL_REPLAY_DIR=replay        replay: answers are read from <dir>/<session>/<task>.json (offline tests)
Every call is logged to sessions/<s>/agents/<task>-<n>.json (prompt, answer, validator result).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import service as svc
from . import stages as stg

PROMPTS = svc.ROOT / "src" / "agents" / "prompts"
MAX_FIX_ROUNDS = 3


API_MODELS = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}
PROVIDERS = {"anthropic": "Claude API (API key)", "claude_cli": "Claude Code CLI (your subscription)"}


def session_llm(session: str | None) -> dict:
    """{'provider': anthropic|claude_cli, 'model': sonnet|opus|haiku} chosen for this session, or {}."""
    if not session:
        return {}
    try:
        return svc.load_state(session).get("llm") or {}
    except svc.StepError:
        return {}


def config(session: str | None = None) -> dict:
    cfg = {}
    p = svc.ROOT / "config.json"
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8"))
    env = {"provider": os.environ.get("SDL_LLM"), "model": os.environ.get("SDL_MODEL"),
           "api_key": os.environ.get("ANTHROPIC_API_KEY"), "replay_dir": os.environ.get("SDL_REPLAY_DIR")}
    cfg.update({k: v for k, v in env.items() if v})
    cfg.setdefault("provider", "anthropic")
    cfg.setdefault("model", "claude-sonnet-5-5")
    cfg.setdefault("replay_dir", "replay")
    ov = session_llm(session)
    if ov and os.environ.get("SDL_LLM") != "replay":     # the replay demo always stays offline
        cfg["provider"] = ov.get("provider", cfg["provider"])
        alias = ov.get("model", "sonnet")
        cfg["model"] = alias if cfg["provider"] == "claude_cli" else API_MODELS.get(alias, alias)
    return cfg


def cli_path() -> str | None:
    import shutil
    return shutil.which("claude")


def provider_info(session: str | None = None, provider: str | None = None) -> dict:
    c = config(session)
    if provider:
        c["provider"] = provider
    if c["provider"] == "claude_cli":
        exe = cli_path()
        return {"provider": "claude_cli", "model": c["model"], "ready": bool(exe),
                "hint": "" if exe else "Claude Code CLI not found: install it, then run `claude auth login` in a terminal"}
    ok = c["provider"] == "replay" or bool(c.get("api_key"))
    return {"provider": c["provider"], "model": c["model"], "ready": ok,
            "hint": "" if ok else "set ANTHROPIC_API_KEY (environment or config.json)"}


# ------------------------------------------------------------------ progress (shown on the working screen)
_tls = threading.local()


def progress(msg: str):
    fn = getattr(_tls, "log", None)
    if fn:
        try:
            fn(msg)
        except Exception:  # noqa: BLE001
            pass


def logged(fn):
    """Wrap an agent entry point so it accepts log=callback (used by the MCP servers)."""
    def wrapper(*a, log=None, **k):
        _tls.log = log
        try:
            return fn(*a, **k)
        finally:
            _tls.log = None
    wrapper.__name__ = fn.__name__
    return wrapper


# ------------------------------------------------------------------ LLM call
def _usage(rec: dict):
    """Per-call usage (seconds, tokens, cost estimate); _log attaches it to the next agent log record."""
    if not hasattr(_tls, "usage"):
        _tls.usage = []
    _tls.usage.append(rec)


def _call(system: str, messages: list[dict], session: str, task: str, max_tokens: int = 8000) -> str:
    c = config(session)
    t0 = time.perf_counter()
    if c["provider"] == "claude_cli":
        out = _call_cli(c, system, messages)
        _usage({"provider": "claude_cli", "model": c["model"], "seconds": round(time.perf_counter() - t0, 1),
                **getattr(_tls, "cli_usage", {})})
        return out
    if c["provider"] == "replay":
        d = Path(c["replay_dir"])
        d = d if d.is_absolute() else svc.ROOT / d
        f = d / session / f"{task}.json"
        if not f.exists():                            # "paper_2" replays the answers of "paper"
            f = d / re.sub(r"_\d+$", "", session) / f"{task}.json"
        if not f.exists():                            # any session on the same paper (by PDF file name)
            pdf = (svc.load_state(session).get("paper") or {}).get("file", "")
            slug = re.sub(r"[^a-z0-9]+", "_", Path(pdf).stem.lower()).strip("_")
            f = d / slug / f"{task}.json"
            if not f.exists() and slug and d.is_dir():   # "minerva_2206_14858" -> replay/minerva/
                f = next((p / f"{task}.json" for p in sorted(d.iterdir()) if p.is_dir() and slug.startswith(p.name)), f)
        if not f.exists():
            raise svc.StepError(f"no replayed answer for '{task}' (replay mode only works for the demo paper "
                                f"examples/papers/minerva_2206.14858.pdf; use a live model for other papers)")
        return f.read_text(encoding="utf-8")
    if not c.get("api_key"):
        raise svc.StepError("no LLM API key: set ANTHROPIC_API_KEY (or SDL_LLM=replay for offline tests)")
    import anthropic
    client = anthropic.Anthropic(api_key=c["api_key"], timeout=anthropic.Timeout(600.0, connect=20.0, read=120.0),
                                 max_retries=2)
    # the long, stable part (system prompt + the first message with the paper) is cached for the fix rounds
    sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    msgs = json.loads(json.dumps(messages))
    first = msgs[0]["content"]
    if isinstance(first, str):
        msgs[0]["content"] = [{"type": "text", "text": first}]
    msgs[0]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    parts, last = [], time.time()
    progress("Claude is thinking…")
    with client.messages.stream(model=c["model"], max_tokens=max_tokens, system=sys_blocks, messages=msgs) as st:
        for chunk in st.text_stream:                 # streaming: no long silent wait, and we can show progress
            parts.append(chunk)
            if time.time() - last > 1.5:
                last = time.time()
                n = sum(len(x) for x in parts)
                progress(f"Claude is writing… {n:,} characters")
        u = st.get_final_message().usage
    _usage({"provider": "anthropic", "model": c["model"], "seconds": round(time.perf_counter() - t0, 1),
            "tokens_in": (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0), "tokens_out": u.output_tokens})
    return "".join(parts)


def _call_cli(c: dict, system: str, messages: list[dict]) -> str:
    """The same call through the logged-in Claude Code CLI (`claude -p`): uses the person's subscription,
    no API key. Images go to a temp folder and are read with the Read tool. The instructions travel in the
    prompt (stdin), so long system prompts never hit the Windows command-line limit."""
    import base64
    import subprocess
    import tempfile
    exe = cli_path()
    if not exe:
        raise svc.StepError("Claude Code CLI not found: install it and run `claude auth login` "
                            "(or choose the Claude API on the first screen)")
    with tempfile.TemporaryDirectory() as td:
        parts, imgs = [], []
        for m in messages:
            blocks = [{"type": "text", "text": m["content"]}] if isinstance(m["content"], str) else m["content"]
            texts = []
            for b in blocks:
                if b.get("type") == "image":
                    ext = "png" if "png" in b["source"].get("media_type", "") else "jpg"
                    p = Path(td) / f"image_{len(imgs) + 1:02d}.{ext}"
                    p.write_bytes(base64.b64decode(b["source"]["data"]))
                    imgs.append(p)
                    texts.append(f"[image {len(imgs)}: {p}]")
                else:
                    texts.append(b.get("text", ""))
            head = "REQUEST" if m["role"] == "user" else "YOUR PREVIOUS ANSWER"
            parts.append(f"===== {head} =====\n" + "\n".join(texts))
        prompt = "<instructions>\n" + system + "\n</instructions>\n\n" + "\n\n".join(parts)
        if imgs:
            prompt += f"\n\nOpen every image listed above ({len(imgs)}) with the Read tool before you answer."
        cmd = [exe, "-p", "--output-format", "json", "--model", c["model"],
               "--system-prompt", "You are one component of a slide-making pipeline. Follow the <instructions> in "
                                  "the message exactly and reply with exactly what they ask for (only JSON when asked).",
               "--tools", "Read" if imgs else "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
               "--disable-slash-commands", "--no-session-persistence", "--max-turns", str(len(imgs) + 4)]
        if imgs:
            cmd += ["--allowedTools", "Read", "--add-dir", td]
        progress(f"Claude (Claude Code CLI, {c['model']}) is thinking…" + (f" – looking at {len(imgs)} images" if imgs else ""))
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=td, timeout=int(os.environ.get("SDL_CLI_TIMEOUT", "1200")),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise svc.StepError("Claude Code CLI gave no answer: " + (proc.stderr or proc.stdout or "")[-400:]) from None
    if data.get("is_error"):
        raise svc.StepError("Claude Code CLI: " + str(data.get("result") or data)[:400])
    u = data.get("usage") or {}
    _tls.cli_usage = {"tokens_in": sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                                "cache_creation_input_tokens")),
                      "tokens_out": u.get("output_tokens"),
                      "est_api_cost_usd": data.get("total_cost_usd")}   # what the same call would cost on the API
    return data.get("result") or ""


def _json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"answer is not valid JSON ({e})") from e


def _log(session: str, task: str, record: dict):
    d = svc.sdir(session) / "agents"
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob(f"{task}-*.json"))) + 1
    usage, _tls.usage = getattr(_tls, "usage", []), []
    if usage:
        record = record | {"usage": usage}
    (d / f"{task}-{n}.json").write_text(json.dumps(record | {"time": time.strftime("%Y-%m-%d %H:%M:%S")},
                                                   indent=1, ensure_ascii=False), encoding="utf-8")


def _loop(session: str, task: str, system: str, user: str, check, max_tokens: int = 8000,
          images: list[tuple[str, str]] | None = None) -> tuple[dict, dict]:
    """Ask, parse, check; feed errors back up to MAX_FIX_ROUNDS times. check(data) -> {'ok','errors',...}.
    images: [(caption, path)] shown to the model with the first message."""
    first = user
    if images:
        first = [{"type": "text", "text": user}]
        for cap, path in images:
            first += [{"type": "text", "text": cap}, _image_block(path)]
    messages = [{"role": "user", "content": first}]
    last = None
    for rnd in range(MAX_FIX_ROUNDS + 1):
        text = _call(system, messages, session, task, max_tokens)
        try:
            data = _json(text)
            res = check(data)
        except (ValueError, svc.StepError) as e:
            data, res = None, {"ok": False, "errors": [str(e)], "warnings": []}
        prompt = messages[-1]["content"]
        if not isinstance(prompt, str):
            prompt = " ".join(b.get("text", "[image]") for b in prompt)
        _log(session, task, {"round": rnd, "prompt": prompt[:20000], "answer": text, "check": res})
        last = (data, res)
        if res["ok"]:
            return data, res | {"rounds": rnd + 1}
        if config(session)["provider"] == "replay":
            break
        if rnd < MAX_FIX_ROUNDS:
            progress(f"The checker found {len(res['errors'])} problem{'s' if len(res['errors']) != 1 else ''} – "
                     f"Claude is fixing them (round {rnd + 2} of {MAX_FIX_ROUNDS + 1})")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "The validator rejected this:\n- " + "\n- ".join(res["errors"]) +
                      "\nReturn the corrected, complete JSON only."}]
    raise svc.StepError(f"{task}: the agent could not produce valid output: " + "; ".join(last[1]["errors"]))


def _image_block(path: str, max_px: int = 900) -> dict:
    import base64
    import io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _section(md: str, stage: str) -> str:
    """General part of design_agent.md + the section of one stage."""
    head = md.split("\n## ")[0]
    sec = next((s for s in md.split("\n## ")[1:] if s.startswith(stage)), "")
    return head + "\n## " + sec


# ------------------------------------------------------------------ agents
def write_brief(session: str) -> dict:
    text = svc.paper_text(session)[:120000]
    st = svc.load_state(session)
    user = f"Paper ({(st.get('paper') or {}).get('file', '')}):\n\n{text}"

    def check(d):
        try:
            svc.save_brief(session, d)
            return {"ok": True, "errors": [], "warnings": []}
        except svc.StepError as e:
            return {"ok": False, "errors": [str(e)], "warnings": []}
    data, res = _loop(session, "brief", _prompt("theme_agent.md"), user, check, 3000)
    return {"brief": data, "rounds": res["rounds"]}


def _spec_summary(session: str) -> str:
    try:
        deck = svc.load_spec(session)
    except svc.StepError:
        return "(no spec yet)"
    lines = []
    for s in deck["slides"]:
        c = s.get("content", {})
        t = re.sub(r"==|\*\*", "", str(c.get("title") or c.get("text") or c.get("message") or ""))
        lines.append(f"- {s['type']}: {t[:90]}")
    return "\n".join(lines)


def propose_options(session: str, stage: str, feedback: str = "") -> dict:
    base = svc.stage_base(session, stage)
    st = svc.load_state(session)
    brief = svc.get_brief(session) or {}
    ko = st.get("kickoff", {})
    parts = [f"Stage: {stage}",
             "Brief:\n" + json.dumps(brief, ensure_ascii=False, indent=1),
             "Direction the human picked: " + (ko.get("direction_text") or "no preference"),
             "Kick-off notes: " + json.dumps(ko.get("notes", ""), ensure_ascii=False)]
    if stage in ("layout", "motion"):
        parts.append("Slides of the talk (type: title):\n" + _spec_summary(session))
    chosen = {k: v for k, v in (base["tokens"] or {}).items() if k != "brand"}
    if chosen:
        parts.append("Choices made so far (build on them, do not repeat them):\n" + json.dumps(chosen, ensure_ascii=False))
    prev = svc.get_options(session, stage)
    if feedback and prev:
        parts.append("The human rejected these 4 options:\n" + json.dumps(prev, ensure_ascii=False) +
                     f"\nHis feedback: {feedback}\nPropose 4 new options that answer the feedback.")
    elif feedback:
        parts.append(f"Extra wish from the human: {feedback}")
    elif prev:                                         # "Regenerate": the human wants genuinely new ideas
        parts.append("The human asked for NEW options. These 4 were already shown – do not repeat them or make close "
                     "variants; explore other directions (other hues, other fonts, other ideas):\n" +
                     json.dumps([{k: v for k, v in o.items() if k != "rationale"} for o in prev.get("options", [])],
                                ensure_ascii=False))
    user = "\n\n".join(parts)
    system = _section(_prompt("design_agent.md"), stage)
    data, res = _loop(session, f"options_{stage}", system, user,
                      lambda d: svc.validate_options(session, stage, dict(d, stage=stage)))
    if stage == "motion":
        data = direct_motion(session, data)
    saved = svc.save_options(session, stage, data)
    st = svc.load_state(session)
    st.setdefault("feedback", {}).setdefault(stage, [])
    if feedback:
        st["feedback"][stage].append(feedback)
    st.get("renders", {}).pop(stage, None)
    svc.save_state(session, st)
    return {"stage": stage, "options": [{"key": o["key"], "label": o.get("label", ""), "rationale": o.get("rationale", "")}
                                        for o in data["options"]],
            "warnings": saved["warnings"], "rounds": res["rounds"], "file": saved["file"]}


def tweak_patch(session: str, stage: str, key: str, instruction: str) -> dict:
    data = svc.get_options(session, stage)
    o = next((x for x in (data or {}).get("options", []) if x["key"] == key), None)
    if o is None:
        raise svc.StepError(f"no option '{key}' in {stage}")
    base = svc.stage_base(session, stage)
    ctx = {"stage": stage, "option": o, "choices_so_far": base["tokens"]}
    user = json.dumps(ctx, ensure_ascii=False, indent=1) + f"\n\nTweak: {instruction}"

    def check(d):
        p = d.get("patch")
        if not isinstance(p, dict) or not p:
            return {"ok": False, "errors": ["'patch' must be a non-empty object"], "warnings": []}
        o2 = json.loads(json.dumps(o))
        o2.setdefault(stg.SECTION[stage], {}).update(p)
        try:
            b = stg.compose(stg.with_option(base["tokens"], stage, o2))
            if stage in ("palette", "type"):
                from . import options as opt_mod
                errs = opt_mod.ensure_fonts(b) + opt_mod.check_brand(b)[0]
                return {"ok": not errs, "errors": errs, "warnings": []}
        except Exception as e:
            return {"ok": False, "errors": [str(e)], "warnings": []}
        return {"ok": True, "errors": [], "warnings": []}
    d, _ = _loop(session, f"tweak_{stage}", _prompt("tweak_agent.md"), user, check, 2000)
    return {"patch": d["patch"], "explanation": d.get("explanation", "")}


READ_SYSTEM = """You transcribe the pages of a document that has no text layer (slides or scans, given as images).
For every page, in order, write:
[page N]
all text on the page, faithfully, in reading order: headings, bullets, labels, numbers, formulas (LaTeX in $...$),
tables as markdown tables; then one line per figure or chart: [Figure: what it shows, with every number you can read].
Do not summarise, do not add anything that is not on the page. Plain text only."""


def read_document(session: str, index: int, batch: int = 4) -> dict:
    """Claude reads a picture-only PDF (e.g. slides exported as images) page by page; the text replaces the
    empty text layer, so every agent (theme, content, checkers) can use the document."""
    import base64
    import pymupdf
    srcs = svc.sources(session)
    if not 0 <= index < len(srcs):
        raise svc.StepError(f"no document #{index}")
    doc = pymupdf.open(str(svc.sdir(session) / srcs[index]["file"]))
    pages, out = len(doc), []
    for a in range(0, pages, batch):
        content = [{"type": "text", "text": f"Pages {a + 1}–{min(pages, a + batch)} of {pages}:"}]
        for k in range(a, min(pages, a + batch)):
            pix = doc[k].get_pixmap(dpi=110)
            content += [{"type": "text", "text": f"[page {k + 1}]"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                     "data": base64.b64encode(pix.tobytes("png")).decode()}}]
        progress(f"Claude is reading pages {a + 1}–{min(pages, a + batch)} of {pages}…")
        text = _call(READ_SYSTEM, [{"role": "user", "content": content}], session, "read", 8000)
        _log(session, "read", {"pages": [a + 1, min(pages, a + batch)], "answer": text})
        out.append(text.strip())
    full = "\n\n".join(out)
    if len(full.strip()) < 40:
        raise svc.StepError("Claude could not read any text from this PDF")
    s = svc.save_read_text(session, index, full)
    return {"index": index, "chars": s["chars"], "pages": pages}


def draft_spec(session: str) -> dict:
    """The content agent (stand-in for the team's): the slide spec from the paper, with the paper's figures,
    to the kick-off's slide count and character limits."""
    st = svc.load_state(session)
    if "kickoff" not in st:
        raise svc.StepError("run the kick-off first")
    k = st["kickoff"]
    d = svc.sdir(session) / "spec"
    d.mkdir(parents=True, exist_ok=True)
    figs = svc.paper_figures(session)
    fig_list = [{"image": "../paper/figures/" + Path(f["path"]).name, "label": f["label"], "caption": f["caption"],
                 "aspect_w_over_h": f["aspect"]} for f in figs]
    rules = _prompt("content_spec_from_paper.md").replace("{minutes}", str(k["talk_minutes"])) \
        .replace("{n_logical}", str(k["logical_slides"])) \
        .replace("{model_label}", {"claude_cli": "Claude via Claude Code", "anthropic": "Claude API"}
                 .get(config(session)["provider"], "Claude"))
    example = (svc.ROOT / "fixture" / "geora_deck.json")
    system = rules + "\n\nCharacter limits per slide type (visible text):\n" + json.dumps(k["char_limits"])
    if example.exists():
        system += "\n\nFormat example (another paper):\n" + example.read_text(encoding="utf-8")[:9000]
    brief = svc.get_brief(session) or {}
    team = (st.get("team") or {}).get("members") or []
    closing = (st.get("team") or {}).get("closing") or {"title": "Thank you", "subtitle": "Questions?"}
    srcs = svc.sources(session)
    docs = "\n".join(f"- Document {i} [{s['role']}] {s.get('title', '')}: {s.get('comment') or '(no comment)'}"
                     for i, s in enumerate(srcs, 1)) if len(srcs) > 1 else ""
    user = ("Brief:\n" + json.dumps(brief, ensure_ascii=False) +
            f"\n\nSlides: exactly {k['logical_slides']} slides that are not section dividers (dividers come on top)." +
            (f"\nPresenters: {', '.join(team)} – the title slide's authors line ends with "
             f"'· presented by {', '.join(team)}'." if team else "") +
            "\nThe last four slides, in this order: closing_takeaways, credits, the workflow diagram (how the slides "
            f"were made), then closing_thanks as the very last slide with title {json.dumps(closing['title'])} and "
            f"subtitle {json.dumps(closing['subtitle'])}." +
            (f"\n\nDocuments (use each one as its comment says; numbers of documents with role own_results are the "
             f"team's own results, label them as ours):\n{docs}" if docs else "") +
            "\n\nThe paper's figures (use the 'image' path exactly as given; you also see each one below):\n" +
            json.dumps(fig_list, ensure_ascii=False, indent=1) + "\n\nDocuments:\n\n" + svc.sources_text(session, 120000))
    images = [(f"{f['label']}: {f['caption'][:160]}", f["path"]) for f in figs[:10]]
    data, res = _loop(session, "spec", system, user, lambda x: svc.check_spec(session, x, d), 16000, images=images)
    p = d / "ai_content.json"
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return svc.import_spec(session, str(p), placeholder=True) | {"rounds": res["rounds"]}


# ------------------------------------------------------------------ morph director
def _slides_for_director(session: str) -> list[dict]:
    deck = svc.load_spec(session)
    out = []
    for s in deck["slides"]:
        c = s.get("content", {})
        t = re.sub(r"==|\*\*|\$", "", str(c.get("title") or c.get("text") or c.get("message") or c.get("number") or ""))
        out.append({"id": s["id"], "type": s["type"], "section": s.get("section", ""), "title": t[:100]})
    return out


def direct_motion(session: str, data: dict) -> dict:
    """Morph director: per-slide transitions for every option; for the canvas world, which world fits the paper."""
    from . import canvas as cv
    slides = _slides_for_director(session)
    ids = {s["id"] for s in slides}
    brief = svc.get_brief(session) or {}
    opts = [{"key": o["key"], "label": o.get("label"), "motion": o.get("motion")} for o in data["options"]]
    brand = svc.tokens(session)[0]
    sections = []
    for sl in slides:
        if sl["type"] != "title" and sl.get("section") and (not sections or sections[-1] != sl["section"]):
            sections.append(sl["section"])
    n_topics = max(len(sections), 1)
    canvas_info = {"sections_in_order": sections, "circle_radius": round(cv._radius(n_topics) * cv.SVG_W / cv.WW),
                   "canvas_background": "#" + cv.world_bg(brand),
                   "palette": {k: "#" + getattr(brand, k) for k in ("bg", "surface", "text", "muted", "ours", "baseline",
                                                                   "highlight", "accent2")}}
    user = ("Brief:\n" + json.dumps(brief, ensure_ascii=False) + "\n\nSlides (in order):\n" +
            json.dumps(slides, ensure_ascii=False, indent=0) + "\n\nMotion options:\n" + json.dumps(opts, ensure_ascii=False) +
            "\n\nFor a custom canvas world:\n" + json.dumps(canvas_info, ensure_ascii=False))

    def check(d):
        errs = []
        plans = d.get("plans")
        if not isinstance(plans, dict):
            return {"ok": False, "errors": ["missing 'plans' object"], "warnings": []}
        for o in data["options"]:
            p = plans.get(o["key"])
            if not isinstance(p, dict):
                errs.append(f"no plan for option {o['key']}")
                continue
            bad = [k for k, v in (p.get("transitions") or {}).items() if v not in ("morph", "fade")]
            if bad:
                errs.append(f"{o['key']}: transitions must be morph|fade ({', '.join(bad[:3])})")
            if (o.get("motion") or {}).get("mode") == "world":
                w = p.get("world") or {}
                if w.get("template") not in cv.KINDS:
                    errs.append(f"{o['key']}: world.template must be one of {', '.join(cv.KINDS)}")
                elif w["template"] == "custom":
                    errs += [f"{o['key']}: custom world: {e}" for e in cv.check_custom(w.get("custom") or {}, n_topics, brand)]
                if len(str(w.get("core", ""))) > 12:
                    errs.append(f"{o['key']}: world.core must be at most 12 characters")
        return {"ok": not errs, "errors": errs, "warnings": []}
    d, _ = _loop(session, "director", _prompt("morph_director.md"), user, check, 16000)
    out = json.loads(json.dumps(data))
    for o in out["options"]:
        p = d["plans"].get(o["key"], {})
        m = o.setdefault("motion", {})
        m["plan"] = {"transitions": p.get("transitions", {})}
        if m.get("mode") == "world" and p.get("world"):
            m.setdefault("world", {}).update({k: v for k, v in p["world"].items() if k in ("template", "core", "custom")})
    return out


# ------------------------------------------------------------------ visual judge (VLM)
def judge_deck(session: str, pngs: list[str], ids: list[str], label: str = "deck") -> dict:
    """Claude looks at the rendered slides and reports problems per slide (+ a fix where we can apply one)."""
    import base64
    from io import BytesIO
    from PIL import Image
    from . import templates
    variants = {k: list(v) for k, v in templates.LIBRARY.items()}
    system = _prompt("visual_judge.md").replace("{variants}", json.dumps(variants))
    c = config(session)
    content = [{"type": "text", "text": f"Deck: {label}. {len(pngs)} slides follow, in order; slide n has id ids[n-1].\n"
                                        f"ids = {json.dumps(ids)}"}]
    if c["provider"] != "replay":
        for k, p in enumerate(pngs, 1):
            im = Image.open(svc.ROOT / p).convert("RGB")
            im.thumbnail((1000, 1000))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=80)
            content.append({"type": "text", "text": f"Slide {k} (id {ids[k - 1]}):"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.b64encode(buf.getvalue()).decode()}})

    try:                                               # the source text, so text fixes can name it exactly
        spec = {sl["id"]: sl.get("content") for sl in svc.load_spec(session)["slides"]}
        src = {i: spec.get(i.split(".")[0]) for i in ids}
        content.append({"type": "text", "text": "Source text per slide id:\n" + json.dumps(
            {k: {kk: vv for kk, vv in (v or {}).items() if kk not in ("image", "image_name")} for k, v in src.items()},
            ensure_ascii=False)[:30000]})
    except svc.StepError:
        pass

    def check(d):
        if not isinstance(d.get("slides"), list) or "score" not in d:
            return {"ok": False, "errors": ["need {'score': 1-10, 'summary': ..., 'slides': [...]}"], "warnings": []}
        return {"ok": True, "errors": [], "warnings": []}
    messages = [{"role": "user", "content": content}]
    last = None
    for rnd in range(2):
        text = _call(system, messages, session, "judge", 6000)
        try:
            d = _json(text)
            res = check(d)
        except ValueError as e:
            d, res = None, {"ok": False, "errors": [str(e)], "warnings": []}
        _log(session, "judge", {"round": rnd, "prompt": f"{len(pngs)} slide images", "answer": text, "check": res})
        last = res
        if res["ok"]:
            return d
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Invalid: " + "; ".join(res["errors"]) + ". Return the JSON only."}]
    raise svc.StepError("judge: " + "; ".join(last["errors"]))


def review(session: str, target: str = "deck") -> dict:
    pngs, ids, label = svc.judge_inputs(session, target)
    res = judge_deck(session, pngs, ids, label)
    return svc.save_judge(session, target, res)


# ------------------------------------------------------------------ polish: change requests on the finished deck
REFINE_SYSTEM = """You apply a presenter's change requests to a finished slide deck (a JSON slide spec that a
deterministic design pipeline lays out). The humans have already approved everything else, so:

THE ONE RULE: change ONLY what a request asks for. Every slide, slot, word, number and note that no request
mentions stays exactly as it is - copy it verbatim. Do not "improve" anything on the side, do not fix
style elsewhere, do not reword a title nobody asked about. A request written under a slide concerns that
slide only. The general request (if any) is the only thing that may touch several slides, and only as far
as it literally says.

How to answer: JSON only,
{"changes": [ ... ], "summary": ["<slide id>: what you changed, in a few words", ...],
 "not_done": ["what you could not do here and why (e.g. colours or fonts of the whole deck: go back to that
               design step)"]}
You have full rights over the finished slides: content, layout AND design. Almost every request can be done;
put something in not_done only if PowerPoint cannot show it at all - never send the human back to an
earlier step for something you can do with the operations below.
Change operations:
- {"op": "update", "id": "<id>", "content": {...the COMPLETE content of that slide, every key, with only the
   requested edit applied...}, "variant": "<optional: another layout of the same type>", "notes": "<optional>"}
  Omit "content" when only the layout (variant) or the notes change. Leave out "variant" to keep the layout.
- {"op": "delete", "id": "<id>"}   (not the title or credits slide)
- {"op": "add", "after": "<id>", "slide": {"type": ..., "content": {...}, "variant": optional, "notes": ...}}
- {"op": "move", "id": "<id>", "after": "<id>"}
- {"op": "design", "id": "<id>", "set": [...], "add": [...], "bg": "RRGGBB"}  free design edits on one slide,
  applied AFTER the layout, so they always win. The element list of every requested slide is given (refs,
  positions in inches on a 13.333 x 7.5 canvas, styles):
    "set": [{"ref": "<ref>", "x":, "y":, "w":, "h":,   (move / resize; give only what changes)
             "style": {"size": pt, "bold": bool, "color": "RRGGBB" or a brand role (text, muted, ours, baseline,
                       highlight, accent2, bg, surface), "fill": same, "font": "heading" | "body" | "mono" | a family,
                       "align": "l"|"c"|"r", "anchor": "t"|"m"|"b", "alpha": 0..1, "corner": "sharp"|"round",
                       "line": colour, "line_w": pt},
             "hide": true,                (remove that element)
             "blur": px, "opacity": 0..1, (images: blur / fade them, e.g. a background picture)
             "text": "..."}]              (only for small non-content texts such as a footer)
    "add": [{"kind": "text"|"rect"|"ellipse"|"line"|"image", "x":, "y":, "w":, "h":, "text":, "style": {...},
             "image": "<figure path from the list>", "behind": true (put it under everything)}]
    "bg": the slide's background colour.
  Example "title to the left": set the title element's x to the margin and style.align "l".
- {"op": "brand", "patch": {"bg"|"surface"|"text"|"muted"|"ours"|"baseline"|"highlight"|"accent2": "RRGGBB",
   "heading_font"|"body_font": "<installed family>", "heading_bold": bool, "sizes": {"title": pt, "body": pt, ...},
   "margin": inches, "title_rule": bool}}   the whole deck's tokens - only for the general request.
Content rules: the keys of each type are in the guide below; stay under the character limit of the type;
numbers only from the documents; math inside $...$ ; never change image paths. "Make it shorter / bigger /
less text" is done with the text (and, if needed, a layout variant of the same type), not by inventing new
slots. If a request is unclear, do the smallest change that clearly satisfies it.
"""


def refine_deck(session: str, requests: list[dict], general: str = "") -> dict:
    """requests: [{"id": spec slide id, "n": slide number shown to the human, "text": the request}]."""
    from . import teamlink, templates
    requests = [r for r in (requests or []) if str(r.get("text", "")).strip()]
    general = (general or "").strip()
    if not requests and not general:
        raise svc.StepError("write at least one change request")
    raw = json.loads(svc.spec_path(session).read_text(encoding="utf-8"))
    ov_path = svc.spec_path(session).parent / "overrides.json"
    ov = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    current = svc.load_spec(session)                   # what is on screen now (overrides applied)
    ids = [s["id"] for s in raw["slides"]]
    asked = {str(r["id"]).split(".")[0] for r in requests}
    bad = asked - set(ids)
    if bad:
        raise svc.StepError(f"unknown slide ids: {', '.join(sorted(bad))} (rebuild the deck and try again)")
    st = svc.load_state(session)
    lim = (st.get("kickoff") or {}).get("char_limits") or {}
    deck_now = [{"id": s["id"], "type": s["type"], "variant": s.get("variant"), "content": s.get("content"),
                 "notes": s.get("notes", ""), "REQUESTED": s["id"] in asked}
                for s in current["slides"]]
    for s in deck_now:                                 # paths are not the agent's business
        c = s["content"] or {}
        if "image" in c:
            c["image"] = Path(c["image"]).name
    user = ("Change requests, per slide:\n" + ("\n".join(
        f"- slide {r.get('n', '?')} (id {str(r['id']).split('.')[0]}): {r['text'].strip()}" for r in requests) or "(none)")
        + f"\n\nGeneral request for the deck: {general or '(none)'}\n\n"
        f"Character limits per slide type: {json.dumps(lim)}\n"
        f"Layout variants per type: {json.dumps({k: list(v) for k, v in templates.LIBRARY.items()})}\n\n"
        f"The deck now (slides marked REQUESTED have a request):\n{json.dumps(deck_now, ensure_ascii=False)}\n\n"
        f"Slide types and their content keys:\n{teamlink._types_guide()}\n\n"
        f"Documents (for facts and numbers):\n{svc.sources_text(session, 30000)}")
    from . import edits
    from .assemble import Assembler
    from .visuals import style_motif
    b, vl, m = svc.tokens(session)
    built = Assembler(b, vl, m, motif=style_motif(b)).build(svc.load_spec(session))
    els = {}
    for s in built:                                    # what is on the requested slides, element by element
        sid = str(s.meta.get("id") or "").split(".")[0]
        if sid in asked:
            els.setdefault(sid, []).append(edits.describe(s))
    figs = sorted(str(p) for p in (svc.spec_path(session).parent / "assets").glob("*.png"))
    figs += [f["path"] for f in svc.paper_figures(session) if Path(f["path"]).exists() and f["path"] not in figs]
    user += (f"\n\nThe deck's design tokens now: {json.dumps(edits.brand_summary(b))}\n"
             f"Elements of the requested slides (physical slides of each id, in order): {json.dumps(els, ensure_ascii=False)}\n"
             f"Figure files you may add: {json.dumps(figs[:30])}")
    images = []
    d = st.get("deck") or {}
    for sid, p in zip(d.get("ids") or [], d.get("pngs") or []):
        if sid.split(".")[0] in asked and (svc.ROOT / p).exists():
            images.append((f"Slide id {sid} as it looks now:", str(svc.ROOT / p)))
    if general and d.get("contact") and (svc.ROOT / d["contact"]).exists():
        images.append(("The whole deck now (contact sheet):", str(svc.ROOT / d["contact"])))

    out = {}

    def check(data):
        ch = data.get("changes")
        if not isinstance(ch, list):
            return {"ok": False, "errors": ["need {'changes': [...], 'summary': [...], 'not_done': [...]}"], "warnings": []}
        errs = []
        if not general:                                # per-slide requests: nothing else may move
            for c in ch:
                if c.get("op") == "brand":
                    errs.append("a 'brand' patch changes every slide: without a general request use 'design' on the "
                                "requested slide instead")
                    continue
                touched = [str(c.get("id") or "")] if c.get("op") != "add" else [str(c.get("after") or "")]
                for t in touched:
                    if t and t not in asked:
                        errs.append(f"slide {t} has no change request: leave it exactly as it is (remove that change)")
        for c in ch:                                   # images stay where they are
            if c.get("op") == "update" and isinstance(c.get("content"), dict) and "image" in c["content"]:
                c["content"].pop("image")
        spec, ov2, e2 = svc.refine_apply(raw, ch, ov)
        errs += e2
        if not errs:
            touched = {str(c.get("id") or c.get("after") or "") for c in ch}
            errs += svc.refine_validate(session, spec, ov2, touched)
        if errs:
            return {"ok": False, "errors": errs, "warnings": []}
        out.update(spec=spec, ov=ov2)
        return {"ok": True, "errors": [], "warnings": []}

    progress(f"Claude applies {len(requests) + bool(general)} change request{'s' if len(requests) + bool(general) != 1 else ''}…")
    data, _ = _loop(session, "refine", REFINE_SYSTEM, user, check, max_tokens=12000, images=images)
    summary = [str(x) for x in data.get("summary") or []]
    not_done = [str(x) for x in data.get("not_done") or []]
    rf = svc.refine_commit(session, out["spec"], out["ov"], requests, general, summary, not_done)
    return {"round": rf["round"], "summary": summary, "not_done": not_done,
            "changes": len(data["changes"])}
