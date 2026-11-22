"""claude_cli provider against a stand-in `claude` executable (no real CLI or login needed)."""
import json
import sys

from pydantic import BaseModel

FAKE_CLI = r'''
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
assert "-p" in args and args[args.index("--output-format") + 1] == "json"
assert "--json-schema" in args and args[args.index("--tools") + 1] == ""
print(json.dumps({"type": "result", "is_error": False, "result": "",
                  "structured_output": {"answer": prompt.upper()},
                  "usage": {"input_tokens": 5, "output_tokens": 3}, "total_cost_usd": 0.001}))
'''


class Out(BaseModel):
    answer: str


def test_claude_cli_backend(tmp_path, monkeypatch):
    (tmp_path / "fake_claude.py").write_text(FAKE_CLI)
    shim = tmp_path / "claude.cmd"
    shim.write_text(f'@"{sys.executable}" "{tmp_path / "fake_claude.py"}" %*\n')
    monkeypatch.setenv("PATH", f"{tmp_path};" + __import__("os").environ["PATH"])
    monkeypatch.setenv("P2S_RUNS_DIR", str(tmp_path / "runs"))
    import importlib, p2s.paths, p2s.tracelog, p2s.llm
    for m in (p2s.paths, p2s.tracelog, p2s.llm):
        importlib.reload(m)

    cfg = {"provider": "claude_cli", "model": "sonnet"}
    out = p2s.llm._claude_cli_call(cfg, Out, "sys", "hello", "r1", "test", None, 0)
    assert out.answer == "HELLO"
    line = json.loads((tmp_path / "runs" / "r1" / "trace.jsonl").read_text().splitlines()[0])
    assert line["model"] == "claude_cli:sonnet" and line["tokens_out"] == 3
