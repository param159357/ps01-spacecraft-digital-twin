"""Generate or reuse the labelled Phase 6 synthetic calendar month.

Usage from ``backend``::

    ./.venv/Scripts/python.exe -m scripts.generate_history --month 2026-08
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.analysis.synthetic import SyntheticHistorySpec, generate_synthetic_month  # noqa: E402
from app.config.loader import load_configuration  # noqa: E402
from app.storage.db import session_scope  # noqa: E402


def _month(value: str) -> date:
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must be YYYY-MM") from exc
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bounded Phase 6 history evidence.")
    parser.add_argument("--month", type=_month, required=True)
    parser.add_argument("--seed", type=int, default=6026)
    parser.add_argument("--issue-weekday", type=int, default=1)
    parser.add_argument("--issue-hour-utc", type=int, default=10)
    parser.add_argument("--issue-duration-minutes", type=int, default=20)
    args = parser.parse_args()
    spec = SyntheticHistorySpec(
        month=args.month,
        seed=args.seed,
        issue_weekday=args.issue_weekday,
        issue_hour_utc=args.issue_hour_utc,
        issue_duration_minutes=args.issue_duration_minutes,
    )
    with session_scope() as db:
        result = generate_synthetic_month(db, load_configuration(), spec)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
