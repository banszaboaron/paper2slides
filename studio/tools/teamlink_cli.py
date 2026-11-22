"""Run sdl.teamlink.check / render in its own process (called by GeoRAProject/design/render.py).

    python tools/teamlink_cli.py check|render <deck.json> <run_dir> <result.json>

Heavy design work (fonts, formulas, pptx) in a worker thread of the MCP server hung on Windows; a child
process with its own main thread and no MCP stdin is what the Studio uses for every render as well.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    cmd, deck_path, run_dir, out = sys.argv[1:5]
    from sdl import teamlink
    deck = json.loads(Path(deck_path).read_text(encoding="utf-8"))
    res = teamlink.check(deck, run_dir) if cmd == "check" else teamlink.render(deck, run_dir)
    Path(out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
