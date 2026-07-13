"""
Story SCRUM-39 - CLI Summary Report (formatting logic).

Deliberately separated from the LangGraph wiring (graph.py) and from the
CLI argument parsing (cli.py): this module's only job is turning a
counts dict into a human-readable string. That makes it trivially
testable without touching MongoDB or LangGraph at all.
"""

from __future__ import annotations

from datetime import datetime

from tender_agent.classify import ALL_CATEGORIES, DELETE, EDIT, INVALID, OTHER, REGISTER, VIEW, CREATE

# The 5 categories the story explicitly asks to report on, in the order
# the business cares about most (create -> register -> edit -> delete -> view).
BUSINESS_CATEGORIES = [CREATE, REGISTER, EDIT, DELETE, VIEW]

_SEPARATOR = "-" * 40


def _format_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def format_report(start_date: datetime, end_date: datetime, counts: dict[str, int]) -> str:
    """
    Build the readable CLI report from a category -> count mapping.

    `counts` is expected to already contain every key in
    tender_agent.classify.ALL_CATEGORIES (that's exactly what
    count_tender_events guarantees) - this function does not defend
    against missing keys on purpose, so a mismatch surfaces immediately
    as a KeyError during development instead of silently printing "0".
    """
    lines = [
        "Tender Board Activity Summary",
        f"Period: {_format_date(start_date)} to {_format_date(end_date)}",
        _SEPARATOR,
    ]

    for category in BUSINESS_CATEGORIES:
        lines.append(f"{category.upper():<12}: {counts[category]:>6}")

    lines.append(_SEPARATOR)
    lines.append(f"{OTHER.upper():<12}: {counts[OTHER]:>6}   (log lines not tied to a tender action)")
    lines.append(f"{INVALID.upper():<12}: {counts[INVALID]:>6}   (malformed/unreadable log records)")
    lines.append(_SEPARATOR)

    total = sum(counts[category] for category in ALL_CATEGORIES)
    lines.append(f"{'TOTAL':<12}: {total:>6}")

    return "\n".join(lines)


def format_error_report(start_date: datetime, end_date: datetime, error_message: str) -> str:
    """Build a readable report for the case where fetching failed entirely."""
    return "\n".join(
        [
            "Tender Board Activity Summary",
            f"Period: {_format_date(start_date)} to {_format_date(end_date)}",
            _SEPARATOR,
            f"ERROR: could not fetch tender board logs - {error_message}",
            _SEPARATOR,
        ]
    )
