"""Local wrapper for the container-native history generator."""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.cli.generate_history import main


if __name__ == "__main__":
    raise SystemExit(main())
