from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path(os.environ.get("P2S_RUNS_DIR", ROOT / "runs"))
CONFIG = ROOT / "config"
CHECKPOINT_DB = RUNS / "checkpoints.sqlite"


def run_dir(run_id: str) -> Path:
    d = RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d
