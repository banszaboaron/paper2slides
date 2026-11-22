"""CLI. Every command prints JSON on stdout so OpenClaw (or anything else) can parse it.

  python -m p2s run --pdf paper.pdf --team "A,B" --venue "ICLR 2026 (Oral)" [--slides 15 --minutes 20
                    --source "report.pdf|own_results|our replication: 2-3 slides"]
  python -m p2s status <run_id>
  python -m p2s resume <run_id> "approve" | "revise: shorten s04" | "abort"
  python -m p2s list
  python -m p2s ui [--port 8766]
  python -m p2s schema            # JSON Schema of the slide spec, for design
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    # Windows consoles default to cp1252; keep JSON output UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(prog="p2s")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--pdf", required=True)
    r.add_argument("--team", default="")
    r.add_argument("--venue", default="")
    r.add_argument("--notes", default="", help="style notes shared with all agents")
    r.add_argument("--run-id")
    r.add_argument("--source", action="append", default=[], metavar="PDF|ROLE|COMMENT",
                   help="another document, e.g. \"report.pdf|own_results|our replication: 2-3 slides\" (repeatable)")
    r.add_argument("--slides", type=int, help="logical slides incl. title, credits, pipeline, thank-you")
    r.add_argument("--minutes", type=int, help="talk length (times the presenter script)")
    r.add_argument("--density", choices=["tight", "medium", "dense"], help="text amount per slide")
    r.add_argument("--closing", default="", help='last slide, e.g. "Thank you for your attention|Questions?"')
    s = sub.add_parser("status"); s.add_argument("run_id")
    rs = sub.add_parser("resume"); rs.add_argument("run_id"); rs.add_argument("answer")
    rs.add_argument("--json", action="store_true", help="answer is a JSON object")
    sub.add_parser("list")
    u = sub.add_parser("ui"); u.add_argument("--port", type=int, default=8766)
    sub.add_parser("schema")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        from .contracts import Deck
        out = Deck.model_json_schema()
    elif a.cmd == "ui":
        import uvicorn
        print(f"Choice UI on http://127.0.0.1:{a.port}", file=sys.stderr)
        uvicorn.run("p2s.ui:app", host="127.0.0.1", port=a.port)
        return 0
    else:
        from . import runner
        if a.cmd == "run":
            team = [t.strip() for t in a.team.split(",") if t.strip()]
            extra = {k: v for k, v in {"n_slides": a.slides, "talk_minutes": a.minutes, "density": a.density}.items() if v}
            if a.closing:
                t, _, sub = a.closing.partition("|")
                extra["closing"] = {"title": t, "subtitle": sub}
            if a.source:
                extra["sources"] = [{"path": a.pdf, "role": "paper", "comment": ""}] + [
                    dict(zip(("path", "role", "comment"), (x.split("|", 2) + ["other", ""])[:3])) for x in a.source]
            out = runner.start(a.pdf, team, a.venue, a.notes, a.run_id, extra=extra)
        elif a.cmd == "status":
            out = runner.status(a.run_id)
        elif a.cmd == "resume":
            out = runner.resume(a.run_id, json.loads(a.answer) if a.json else a.answer)
        else:
            out = runner.list_runs()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
