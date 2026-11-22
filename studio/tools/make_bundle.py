"""Pack the code + fonts into dist/sdl_bundle.zip (what a workflow chat stages from the laptop).

    python tools/make_bundle.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["src", "tools", "tests", "fonts", "references", "mcp_servers", "webapp", "replay", "docs/brief.md",
           "docs/WORKFLOW.md", "README.md", "requirements.txt", "config.example.json", "start_ui.bat", "start_ui_replay.bat"]
SKIP_PARTS = {"__pycache__", "_src", "sessions", "outputs"}


def main():
    out = ROOT / "dist" / "sdl_bundle.zip"
    out.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            p = ROOT / item
            files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.exists() else []
            for f in files:
                rel = f.relative_to(ROOT)
                if SKIP_PARTS & set(rel.parts) or f.suffix == ".pyc":
                    continue
                z.write(f, rel.as_posix())
                n += 1
    print(f"{out} ({n} files, {out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
