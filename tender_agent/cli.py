"""
Story SCRUM-39 - CLI Summary Report (entry point).

This is the ONLY file a user actually runs. It parses command-line
arguments, builds the LangGraph pipeline (graph.py), invokes it once,
and prints the resulting report. It contains no business logic itself -
that separation is what lets fetch/classify/report each be tested in
isolation (SCRUM-37/38/39 test files) without ever needing a CLI.

Usage
-----
    python -m tender_agent.cli --days 30
    python -m tender_agent.cli --start 2026-01-01 --end 2026-01-31
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

from tender_agent.graph import build_graph

DATE_FORMAT = "%Y-%m-%d"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tender_agent",
        description="Fetch, classify, and summarize Tender Board activity logs.",
    )

    # --days and --start/--end are mutually exclusive - you pick ONE way
    # to specify the reporting period.
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--days",
        type=int,
        default=None,
        help="Report on the last N days (default: 30 if no other option is given).",
    )
    group.add_argument(
        "--start",
        type=str,
        default=None,
        help=f"Start date, format {DATE_FORMAT} (requires --end).",
    )

    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help=f"End date, format {DATE_FORMAT} (requires --start).",
    )

    args = parser.parse_args(argv)

    if args.start and not args.end:
        parser.error("--start requires --end")
    if args.end and not args.start:
        parser.error("--end requires --start")

    return args


def resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    """Turn parsed CLI args into a concrete (start_date, end_date) pair."""
    if args.start and args.end:
        try:
            start_date = datetime.strptime(args.start, DATE_FORMAT)
            end_date = datetime.strptime(args.end, DATE_FORMAT)
        except ValueError as exc:
            raise SystemExit(f"Invalid date format - expected {DATE_FORMAT}: {exc}")

        if start_date > end_date:
            raise SystemExit("--start must not be after --end")

        return start_date, end_date

    days = args.days if args.days is not None else 30
    if days <= 0:
        raise SystemExit("--days must be a positive integer")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    return start_date, end_date


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    args = parse_args(argv if argv is not None else sys.argv[1:])
    start_date, end_date = resolve_date_range(args)

    app = build_graph()
    result = app.invoke({"start_date": start_date, "end_date": end_date})

    print(result["report"])

    # Non-zero exit code when the fetch failed, so shell scripts / cron
    # jobs calling this CLI can detect failure without parsing the text.
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
