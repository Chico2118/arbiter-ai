"""Build arbiter-project.zip from the project folder.

Excludes dependencies, caches, build output, local databases and secrets.
Run from anywhere:  python scripts/package.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "arbiter-project.zip"

EXCLUDED_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist",
                 ".vite", "data", ".git", ".idea", ".vscode"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".zip", ".tsbuildinfo", ".log"}
EXCLUDED_NAMES = {".env", "Thumbs.db", ".DS_Store"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    if path.name in EXCLUDED_NAMES:
        return False
    return not any(path.name.endswith(s) for s in EXCLUDED_SUFFIXES)


def main() -> int:
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p))
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, Path("arbiter") / p.relative_to(ROOT))
    print(f"Wrote {OUT} ({len(files)} files, {OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
