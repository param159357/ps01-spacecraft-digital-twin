"""Generate or reuse one bounded synthetic calendar month."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app.analysis.synthetic import SyntheticHistorySpec, generate_synthetic_month
from app.config.loader import load_configuration
from app.storage.db import session_scope


def _month(value: str) -> date:
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must be YYYY-MM") from exc


def _last_completed_month(today: date | None = None) -> date:
    first_of_this_month = (today or datetime.now(UTC).date()).replace(day=1)
    return (first_of_this_month - timedelta(days=1)).replace(day=1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate bounded Phase 6 history evidence.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--month", type=_month, help="Calendar month in YYYY-MM form.")
    selection.add_argument(
        "--completed-month",
        action="store_true",
        help="Use the most recently completed UTC calendar month.",
    )
    parser.add_argument("--seed", type=int, default=6026)
    parser.add_argument("--issue-weekday", type=int, default=1)
    parser.add_argument("--issue-hour-utc", type=int, default=10)
    parser.add_argument("--issue-duration-minutes", type=int, default=20)
    args = parser.parse_args(argv)
    month = _last_completed_month() if args.completed_month else args.month
    spec = SyntheticHistorySpec(
        month=month,
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
