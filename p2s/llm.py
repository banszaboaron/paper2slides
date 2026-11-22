"""Model registry (config/models.yaml) + provider-agnostic structured calls.

`structured_call` asks for JSON matching a Pydantic schema, validates it, and on a
validation error re-asks once with the error. This works with any chat model,
including small local/free ones that have weak tool-calling support.
"""
from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .paths import CONFIG
from .tracelog import log_step

T = TypeVar("T", bound=BaseModel)


@lru_cache
def _config() -> dict:
    """config/models.local.yaml (gitignored, per machine) overrides config/models.yaml."""
    cfg = yaml.safe_load((CONFIG / "models.yaml").read_text(encoding="utf-8"))
    local = CONFIG / "models.local.yaml"
    if local.exists():
        over = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        cfg["default"] = {**cfg.get("default", {}), **over.get("default", {})}
        cfg["roles"] = {**cfg.get("roles", {}), **over.get("roles", {})}
    return cfg


# One switch for every role, set by the Slide Studio (the model chosen on its first screen) or by hand:
#   P2S_LLM_PROVIDER=anthropic|claude_cli   P2S_LLM_MODEL=claude-sonnet-5-5 | sonnet | opus ...
OVERRIDE_DEFAULTS = {"anthropic": {"max_tokens": 16000}, "claude_cli": {"timeout_s": 900}}


def _override() -> dict:
    prov = os.environ.get("P2S_LLM_PROVIDER")
    if not prov:
        return {}
    model = os.environ.get("P2S_LLM_MODEL") or ("sonnet" if prov == "claude_cli" else "claude-sonnet-5-5")
    return {"provider": prov, "model": model, **OVERRIDE_DEFAULTS.get(prov, {})}


def role_cfg(role: str) -> dict:
    cfg = _config()
    c = {**cfg.get("default", {}), **cfg.get("roles", {}).get(role, {})}
    ov = _override()
    if ov:                      # the chosen provider's settings only (newer Claude models reject `temperature`)
        c = dict(ov)
    return c


def get_chat_model(role: str):
    return _chat_model(role, json.dumps(role_cfg(role), sort_keys=True))


@lru_cache
def _chat_model(role: str, key: str):
    c = json.loads(key)
    provider = c["provider"]
    if provider in ("fake", "claude_cli"):
        return None
    from langchain.chat_models import init_chat_model

    kwargs = {k: v for k, v in c.items()
              if k not in ("provider", "model", "api_key_env", "price_in_per_mtok", "price_out_per_mtok")}
    if c.get("api_key_env"):
        kwargs["api_key"] = os.environ[c["api_key_env"]]
    return init_chat_model(c["model"], model_provider=provider, **kwargs)


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return text[start:]


def _cost(c: dict, tin: Optional[int], tout: Optional[int]) -> Optional[float]:
    pi, po = c.get("price_in_per_mtok"), c.get("price_out_per_mtok")
    if pi is None or po is None or tin is None or tout is None:
        return None
    return round((tin * pi + tout * po) / 1e6, 6)


def structured_call(*, role: str, schema: Type[T], system: str, user: str,
                    run_id: str, agent: str, fake: Callable[[], T],
                    images: Optional[list[str]] = None, retries: int = 1) -> T:
    """Call the model for `role` and return a validated `schema` instance."""
    c = role_cfg(role)
    if c["provider"] == "claude_cli":
        return _claude_cli_call(c, schema, system, user, run_id, agent, images, retries)
    if c["provider"] == "anthropic":           # the Anthropic SDK directly (same client as the design side)
        return _anthropic_call(c, schema, system, user, run_id, agent, images, retries)
    model = get_chat_model(role)
    t0 = time.perf_counter()
    if model is None:
        out = fake()
        log_step(run_id, agent, model="fake", tool="llm", input=user, output=out.model_dump(),
                 duration_ms=(time.perf_counter() - t0) * 1000, tokens_in=0, tokens_out=0, cost_usd=0.0)
        return out

    from langchain_core.messages import HumanMessage, SystemMessage

    schema_txt = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys = (f"{system}\n\nReply with ONLY a JSON object that validates against this JSON Schema "
           f"(no prose, no markdown fences):\n{schema_txt}")
    content: list | str = user
    if images:
        import base64
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                base64.b64encode(open(p, "rb").read()).decode()}}
            for p in images]
    messages = [SystemMessage(sys), HumanMessage(content)]

    last_err = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        resp = model.invoke(messages)
        usage = getattr(resp, "usage_metadata", None) or {}
        tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
        text = resp.content if isinstance(resp.content, str) else "".join(
            p.get("text", "") for p in resp.content if isinstance(p, dict))
        try:
            out = schema.model_validate_json(_extract_json(text))
            ok, result = True, out.model_dump()
        except (ValidationError, ValueError) as e:
            ok, result, last_err = False, f"INVALID: {e}", e
        log_step(run_id, agent, model=f"{c['provider']}:{c['model']}", tool="llm", input=user,
                 output=result, duration_ms=(time.perf_counter() - t0) * 1000,
                 tokens_in=tin, tokens_out=tout, cost_usd=_cost(c, tin, tout), attempt=attempt)
        if ok:
            return out
        messages += [resp, HumanMessage(f"That JSON was invalid:\n{last_err}\nReturn the corrected JSON only.")]
    raise RuntimeError(f"{agent}: model did not return valid {schema.__name__}: {last_err}")


# ---------------------------------------------------------------- Claude API backend (anthropic SDK)
def _anthropic_call(c: dict, schema: Type[T], system: str, user: str, run_id: str, agent: str,
                    images: Optional[list[str]], retries: int) -> T:
    """Claude API through the official SDK, streamed (long outputs), JSON validated against `schema`.
    Key: ANTHROPIC_API_KEY. No temperature: the current Claude models only accept the default."""
    import base64
    import anthropic

    client = anthropic.Anthropic(timeout=anthropic.Timeout(900.0, connect=20.0, read=180.0), max_retries=2)
    schema_txt = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys_txt = (f"{system}\n\nReply with ONLY a JSON object that validates against this JSON Schema "
               f"(no prose, no markdown fences):\n{schema_txt}")
    content: list = [{"type": "text", "text": user}]
    for p in images or []:
        mt = "image/png" if str(p).lower().endswith(".png") else "image/jpeg"
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                    "data": base64.b64encode(open(p, "rb").read()).decode()}})
    messages = [{"role": "user", "content": content}]
    last_err = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        with client.messages.stream(model=c.get("model", "claude-sonnet-5-5"), max_tokens=int(c.get("max_tokens", 16000)),
                                    system=sys_txt, messages=messages) as st:
            msg = st.get_final_message()
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        tin, tout = msg.usage.input_tokens, msg.usage.output_tokens
        try:
            out = schema.model_validate_json(_extract_json(text))
            ok, result = True, out.model_dump()
        except (ValidationError, ValueError) as e:
            ok, result, last_err = False, f"INVALID: {e}", e
            if not text.strip():
                last_err = RuntimeError(f"empty answer (stop_reason={msg.stop_reason})")
        log_step(run_id, agent, model=f"anthropic:{c.get('model')}", tool="llm", input=user, output=result,
                 duration_ms=(time.perf_counter() - t0) * 1000, tokens_in=tin, tokens_out=tout,
                 cost_usd=_cost(c, tin, tout), attempt=attempt)
        if ok:
            return out
        messages += [{"role": "assistant", "content": text or "(empty)"},
                     {"role": "user", "content": f"That JSON was invalid:\n{last_err}\nReturn the corrected JSON only."}]
    raise RuntimeError(f"{agent}: Claude did not return valid {schema.__name__}: {last_err}")


# ---------------------------------------------------------------- Claude Code CLI backend
def _cli_schema(node):
    """The CLI's strict JSON Schema check rejects 'prefixItems' (pydantic's fixed-length tuples):
    a tuple becomes a list of the same length (same item type when all items agree)."""
    if isinstance(node, dict):
        node = {k: _cli_schema(v) for k, v in node.items()}
        if "prefixItems" in node:
            items = node.pop("prefixItems")
            node["items"] = items[0] if all(i == items[0] for i in items) else {"anyOf": items}
            node.setdefault("minItems", len(items))
            node.setdefault("maxItems", len(items))
        return node
    if isinstance(node, list):
        return [_cli_schema(v) for v in node]
    return node


def _claude_cli_call(c: dict, schema: Type[T], system: str, user: str, run_id: str, agent: str,
                     images: Optional[list[str]], retries: int) -> T:
    """Use the locally logged-in Claude Code CLI (`claude -p`) as the model.

    Uses whatever `claude` is logged in with (e.g. a Pro/Max subscription via `claude auth login`),
    so no API key is needed; calls count against that plan's usage limits. Personal use only:
    don't expose this to other people as a service.
    """
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which(c.get("bin", "claude"))
    if not exe:
        raise RuntimeError("claude CLI not found on PATH; install Claude Code and run `claude auth login`")
    schema_txt = json.dumps(_cli_schema(schema.model_json_schema()), ensure_ascii=False)
    prompt = user
    tools = ""                                   # pure text model: no tools at all
    if images:                                   # let it look at the PNGs with the Read tool
        tools = "Read"
        prompt += "\n\nImages to inspect (read them with the Read tool):\n" + "\n".join(images)
    cmd = [exe, "-p", "--output-format", "json", "--model", c.get("model", "sonnet"),
           "--system-prompt", system, "--json-schema", schema_txt, "--tools", tools,
           "--disallowedTools", "mcp__*", "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--max-turns", str(c.get("max_turns", 4))]
    if images:
        cmd += ["--allowedTools", "Read"] + sum((["--add-dir", str(Path(p).parent)] for p in images), [])

    last_err = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        # prompt goes through stdin: Windows caps command lines at ~32k chars
        with tempfile.TemporaryDirectory() as cwd:   # neutral cwd: no project CLAUDE.md
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", cwd=cwd, timeout=c.get("timeout_s", 600))
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = {"is_error": True, "result": (proc.stdout or proc.stderr)[-2000:]}
        usage = data.get("usage") or {}
        tin = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) \
            + (usage.get("cache_creation_input_tokens") or 0) or None
        tout = usage.get("output_tokens")
        ok = False
        if not data.get("is_error"):
            try:
                raw = data.get("structured_output")
                out = schema.model_validate(raw) if raw is not None else \
                    schema.model_validate_json(_extract_json(data.get("result", "")))
                ok, result = True, out.model_dump()
            except (ValidationError, ValueError) as e:
                last_err = e
        else:
            last_err = RuntimeError(str(data.get("result"))[:500])
        log_step(run_id, agent, model=f"claude_cli:{c.get('model', 'sonnet')}", tool="llm", input=user,
                 output=result if ok else f"INVALID: {last_err}",
                 duration_ms=(time.perf_counter() - t0) * 1000, tokens_in=tin, tokens_out=tout,
                 cost_usd=0.0, est_api_cost_usd=data.get("total_cost_usd"), attempt=attempt)
        if ok:
            return out
        prompt = f"{prompt}\n\nYour previous answer was invalid: {last_err}\nReturn corrected JSON."
    raise RuntimeError(f"{agent}: claude CLI did not return valid {schema.__name__}: {last_err}")
