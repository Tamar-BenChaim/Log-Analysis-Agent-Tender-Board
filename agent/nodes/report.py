"""
CLI Summary Report formatting logic (originally Story SCRUM-39).

Deliberately separated from the LangGraph wiring (agent/graph.py) and
from the CLI argument parsing (agent/cli.py): this module's only job is
turning a counts dict into a human-readable string. That makes it
trivially testable without touching MongoDB or LangGraph at all.
"""

from __future__ import annotations

import re
from datetime import datetime

from agent.nodes.classify import ALL_CATEGORIES, DELETE, EDIT, INVALID, OTHER, REGISTER, VIEW, CREATE

# The 5 categories the story explicitly asks to report on, in the order
# the business cares about most (create -> register -> edit -> delete -> view).
BUSINESS_CATEGORIES = [CREATE, REGISTER, EDIT, DELETE, VIEW]

_SEPARATOR = "-" * 40

# Access-log lines can carry arbitrarily long URL-encoded query strings
# (e.g. smart-search free-text queries), which would otherwise blow a
# single anomaly entry across many wrapped terminal lines. Cut those
# off after this many characters so every entry stays one line.
_MAX_MESSAGE_LENGTH = 60

# The trailing "STATUS DURATIONms" token on an HTTP access-log line, e.g.
# "GET /tender-board/smart-search?q=... 200 3579ms" -> " 200 3579ms".
# Truncation must never eat into this - the duration is the whole reason
# the line is being reported, so it's kept intact and only the (usually
# long/noisy) part before it gets cut.
_TAIL_PATTERN = re.compile(r"\s+\d+\s+\d+ms\s*$")


def _format_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _truncate(text: str, max_length: int = _MAX_MESSAGE_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    tail_match = _TAIL_PATTERN.search(text)
    tail = tail_match.group(0) if tail_match else ""
    head_length = max(0, max_length - len(tail) - 3)
    return text[:head_length].rstrip() + "..." + tail


def format_report(
    start_date: datetime,
    end_date: datetime,
    counts: dict[str, int],
    error_summary: "dict | None" = None,
    anomalies: "dict | None" = None,
    analysis: "dict | None" = None,
    analysis_unavailable_reason: "str | None" = None,
) -> str:
    """
    Build the readable CLI report from a category -> count mapping.

    `counts` is expected to already contain every key in
    agent.nodes.classify.ALL_CATEGORIES (that's exactly what
    count_tender_events guarantees) - this function does not defend
    against missing keys on purpose, so a mismatch surfaces immediately
    as a KeyError during development instead of silently printing "0".

    `error_summary` (agent.nodes.errors.summarize_errors's return value)
    and `anomalies` (`{"duplicates": [...], "slow_requests": [...]}`)
    are both optional and default to None so existing callers are
    unaffected - when omitted, the report looks exactly as it did
    before this story. Both sections are aggregate-level summaries
    only; neither ever lists individual raw records (that stays
    exclusive to chat mode).

    `analysis` (agent.nodes.analyze.analyze_records's return value, once
    agent.nodes.evaluator.evaluate_analysis has approved it) renders an
    "AI Analysis" section. `analysis_unavailable_reason` is the
    alternative when evaluator_node never approved a response within
    the retry cap - the two are mutually exclusive; passing neither
    (the default) omits the section entirely, exactly as before this
    story.
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

    if error_summary is not None:
        lines.append(_SEPARATOR)
        lines.append("Errors")
        lines.append(f"  Total: {error_summary['total']}")
        for module, count in sorted(error_summary["by_module"].items(), key=lambda kv: -kv[1]):
            lines.append(f"    {module}: {count}")
        for entry in error_summary["recurring"]:
            lines.append(f"  RECURRING x{entry['count']} [{entry['module']}]: {entry['message']}")

    if anomalies is not None:
        lines.append(_SEPARATOR)
        lines.append("Anomalies")
        duplicates = anomalies.get("duplicates", [])
        lines.append(f"  {len(duplicates)} duplicate-submit cluster(s) detected")
        for dup in duplicates:
            lines.append(
                f"    user={dup['user_id']} org={dup['organization_id']} "
                f"tenders={dup['tender_ids']} ({dup['seconds_apart']:.3f}s apart)"
            )
        slow_requests = anomalies.get("slow_requests", [])
        lines.append(f"  {len(slow_requests)} request(s) exceeded the latency threshold")
        for slow in slow_requests:
            lines.append(f"    {slow['duration_ms']}ms: {_truncate(slow['message'])}")

    if analysis is not None:
        lines.append(_SEPARATOR)
        lines.append("AI Analysis")
        lines.append(f"  {analysis['business_logic_notes']}")
        for pattern in analysis["error_patterns"]:
            lines.append(f"  Error pattern: {pattern}")
        for anomaly in analysis["anomalies"]:
            lines.append(f"  Anomaly: {anomaly}")
        lines.append(f"  Confidence: {analysis['confidence']:.2f}")
    elif analysis_unavailable_reason is not None:
        lines.append(_SEPARATOR)
        lines.append("AI Analysis")
        lines.append(f"  Unavailable this run - {analysis_unavailable_reason}")

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
