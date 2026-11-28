"""Make the repository-level test helpers and the reference script importable."""

from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for _path in (TESTS_DIR, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
