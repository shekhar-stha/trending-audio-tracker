"""Copy data/trending.json into docs/ so GitHub Pages can serve it."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "trending.json"
DST = ROOT / "docs" / "trending.json"

DST.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(SRC, DST)
print(f"copied {SRC} -> {DST}")
