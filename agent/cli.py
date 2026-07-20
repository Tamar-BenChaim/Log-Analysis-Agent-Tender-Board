"""
CLI entry point (originally Story SCRUM-39; restructured with a `report`/
`chat` subcommand split in SCRUM-170; `chat` implemented in SCRUM-174).

This is the ONLY file a user actually runs. It parses command-line
arguments and dispatches to one of two subcommands:

    python -m agent.cli report --days 30
    python -m agent.cli report --start 2026-01-01 --end 2026-01-31
    python -m agent.cli chat

`report` builds the LangGraph report pipeline (agent/graph.py), invokes
it once, and prints the resulting report. `chat` builds the interactive
chat graph and runs a simple input() loop, printing the agent's final
answer each turn - it requires a real OPENAI_API_KEY in the environment
(ChatOpenAI is only constructed on first use, not at import time).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

from agent.graph import build_chat_graph, build_graph

DATE_FORMAT = "%Y-%m-%d"
CHAT_EXIT_COMMANDS = {"exit", "quit"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Fetch, classify, and summarize Tender Board activity logs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report", help="Print a one-shot summary report for a date range."
    )
    # --days and --start/--end are mutually exclusive - you pick ONE way
    # to specify the reporting period.
    group = report_parser.add_mutually_exclusive_group()
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
        help=f"Start date, format {DATE_FORMAT.replace('%', '%%')} (requires --end).",
    )
    report_parser.add_argument(
        "--end",
        type=str,
        default=None,
        help=f"End date, format {DATE_FORMAT.replace('%', '%%')} (requires --start).",
    )

    subparsers.add_parser("chat", help="Start an interactive chat session over the logs.")

    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "report":
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


def run_report(args: argparse.Namespace) -> int:
    start_date, end_date = resolve_date_range(args)

    app = build_graph()
    result = app.invoke({"start_date": start_date, "end_date": end_date})

    print(result["report"])

    # Non-zero exit code when the fetch failed, so shell scripts / cron
    # jobs calling this CLI can detect failure without parsing the text.
    return 1 if result.get("error") else 0


def run_chat(args: argparse.Namespace) -> int:
    print("Tender Board chat - ask a question, or type 'exit'/'quit' to leave.")

    app = build_chat_graph()
    state: dict = {"messages": [], "guardrail_flags": []}

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input.lower() in CHAT_EXIT_COMMANDS:
            return 0

        state["messages"].append(("user", user_input))
        state = app.invoke(state)

        print(state["messages"][-1].content)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "report":
        return run_report(args)
    return run_chat(args)


if __name__ == "__main__":
    raise SystemExit(main())
