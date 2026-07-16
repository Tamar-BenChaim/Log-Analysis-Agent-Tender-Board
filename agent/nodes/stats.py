"""
Latency and duplicate-tender detection (Story SCRUM-166).

Both functions here consume the same raw list[dict] records that
agent.nodes.classify does - they are independent analyses run over the
same batch, not a pipeline of one into the other.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

# Trailing "NNNms" token on an HTTP access-log line, e.g.
# "POST /tender-board/smart-create 201 2382ms" -> duration = 2382.
_DURATION_PATTERN = re.compile(r"(\d+)ms\s*$")

DEFAULT_DUPLICATE_WINDOW_SECONDS = 5
DEFAULT_SLOW_THRESHOLD_MS = 2000


def _tender_content_signature(tender: dict[str, Any]) -> Any:
    """
    A hashable signature identifying "the same tender content", used to
    tell a duplicate-submit from two genuinely different tenders created
    close together by the same user.

    Prefers (title, budget) - the two fields most likely to differ
    between two unrelated tenders. Falls back to a sorted, stringified
    dump of the whole tender dict when title/budget are both absent, so
    unusual/future tender shapes still get *some* signature rather than
    crashing or silently comparing equal to everything.
    """
    title = tender.get("title")
    budget = tender.get("budget")
    if title is not None or budget is not None:
        return (title, budget)
    return tuple(sorted((k, str(v)) for k, v in tender.items()))


def find_duplicate_tenders(
    records: list[dict[str, Any]],
    window_seconds: float = DEFAULT_DUPLICATE_WINDOW_SECONDS,
    date_field: str = "timestamp",
) -> list[dict[str, Any]]:
    """
    Detect duplicate-submit clusters: same userId + organizationId +
    tender content, two different tenderId/requestId values, within
    `window_seconds` of each other (default 5s - the one real anomaly
    observed in production was ~600ms apart).

    Only records with `context.tenderId` are considered (create-type
    business events) - HTTP access-log lines and non-tender business
    events have no `context` and are ignored here.

    Returns one entry per detected duplicate pair:
        {"user_id", "organization_id", "tender_ids": [a, b],
         "request_ids": [a, b], "seconds_apart": float}
    """
    candidates = []
    for record in records:
        context = record.get("context")
        timestamp = record.get(date_field)
        if not isinstance(context, dict) or "tenderId" not in context:
            continue
        if not isinstance(timestamp, datetime):
            continue
        tender = context.get("tender") or {}
        candidates.append(
            {
                "user_id": record.get("userId"),
                "organization_id": record.get("organizationId"),
                "tender_id": context.get("tenderId"),
                "request_id": record.get("requestId"),
                "signature": _tender_content_signature(tender),
                "timestamp": timestamp,
            }
        )

    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (candidate["user_id"], candidate["organization_id"], candidate["signature"])
        groups.setdefault(key, []).append(candidate)

    duplicates: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda c: c["timestamp"])
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier["tender_id"] == later["tender_id"]:
                continue
            seconds_apart = (later["timestamp"] - earlier["timestamp"]).total_seconds()
            if seconds_apart <= window_seconds:
                duplicates.append(
                    {
                        "user_id": earlier["user_id"],
                        "organization_id": earlier["organization_id"],
                        "tender_ids": [earlier["tender_id"], later["tender_id"]],
                        "request_ids": [earlier["request_id"], later["request_id"]],
                        "seconds_apart": seconds_apart,
                    }
                )

    return duplicates


def compute_latency_stats(
    records: list[dict[str, Any]],
    message_field: str = "message",
    slow_threshold_ms: int = DEFAULT_SLOW_THRESHOLD_MS,
) -> dict[str, Any]:
    """
    Aggregate-level latency stats parsed from HTTP access-log lines
    (the trailing "NNNms" token). Records with no parseable duration
    (business logger lines, malformed records) are skipped, not errors.

    Returns {"count", "avg_ms", "max_ms", "over_threshold"} where
    over_threshold is a list of {"message", "duration_ms"} for lines
    exceeding `slow_threshold_ms` - this is the single aggregate
    "unusual latency" signal for the summary report; per-endpoint
    percentile breakdowns are a chat-mode tool concern (SCRUM-174).
    """
    durations: list[int] = []
    over_threshold: list[dict[str, Any]] = []

    for record in records:
        message = record.get(message_field)
        if not isinstance(message, str):
            continue
        match = _DURATION_PATTERN.search(message.strip())
        if not match:
            continue
        duration_ms = int(match.group(1))
        durations.append(duration_ms)
        if duration_ms > slow_threshold_ms:
            over_threshold.append({"message": message, "duration_ms": duration_ms})

    if not durations:
        return {"count": 0, "avg_ms": 0.0, "max_ms": 0, "over_threshold": []}

    return {
        "count": len(durations),
        "avg_ms": sum(durations) / len(durations),
        "max_ms": max(durations),
        "over_threshold": over_threshold,
    }
