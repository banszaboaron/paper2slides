"""Turn a live session's agent answers into an offline replay set.

    python tools/record_replay.py minerva_checked minerva      # sessions/minerva_checked -> replay/minerva/

For every design-agent task (brief, options_<stage>, director, judge, read, ...) the answer of the last logged
round that passed its check is copied to replay/<name>/<task>.json. With SDL_LLM=replay the Studio then
answers every agent call of a session called <name> (or <name>_2, ...) from these files: the offline demo
runs the exact recorded answers, with no model and no network.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(session: str, name: str) -> int:
    logs = ROOT / "sessions" / session / "agents"
    if not logs.exists():
        print(f"no agent logs in {logs}")
        return 1
    best: dict[str, tuple[int, str]] = {}
    for f in logs.glob("*-*.json"):
        m = re.fullmatch(r"(.+)-(\d+)", f.stem)
        if not m:
            continue
        task, n = m.group(1), int(m.group(2))
        rec = json.loads(f.read_text(encoding="utf-8"))
        chk = rec.get("check") or {}
        if "answer" not in rec or (isinstance(chk, dict) and chk.get("ok") is False):
            continue
        if task not in best or n > best[task][0]:
            best[task] = (n, rec["answer"])
    out = ROOT / "replay" / name
    out.mkdir(parents=True, exist_ok=True)
    for task, (n, text) in sorted(best.items()):
        (out / f"{task}.json").write_text(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False),
                                          encoding="utf-8")
        print(f"{task:22s} <- {task}-{n}.json")
    print(f"{len(best)} answers -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]) if len(sys.argv) >= 3 else (print(__doc__) or 2))
