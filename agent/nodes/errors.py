"""
Error summarization (Story SCRUM-166).

Groups error records by module and detects genuine recurrence - but
only AFTER de-duplicating a known TypeScript-side double-logging
pattern, so one real failure doesn't get counted twice.

Why de-dup by requestId first
-------------------------------
The backend logs the same underlying error more than once in some call
chains - e.g. `generateTenderData` catches an error, calls
`logger.error`, and re-throws it; `createSmartTender` then catches that
same error and calls `logger.error` again. Both log lines share one
`requestId` (attached automatically to every line of one HTTP request
via AsyncLocalStorage). Grouping straight by (module, message) would
count that single failure as 2, inflating both the total and the
recurrence signal. De-duping first on (requestId, message) collapses
that back to 1 real failure before any counting happens.

Records with no `requestId` (pre-enrichment logs) can't use that key,
so they fall back to (module, message, timestamp rounded to the
nearest second) - a weaker but reasonable proxy for "the same log call
site, fired at effectively the same instant".
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from agent.nodes.classify import _is_error_record

UNKNOWN_MODULE = "unknown"


def _dedupe_key(record: dict[str, Any], message_field: str, date_field: str) -> tuple:
    message = record.get(message_field)
    request_id = record.get("requestId")
    if request_id:
        return ("request", request_id, message)

    timestamp = record.get(date_field)
    rounded = timestamp.replace(microsecond=0) if isinstance(timestamp, datetime) else timestamp
    return ("fallback", record.get("module"), message, rounded)


def _dedupe_error_records(
    records: list[dict[str, Any]], message_field: str, date_field: str
) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = _dedupe_key(record, message_field, date_field)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def summarize_errors(
    records: list[dict[str, Any]],
    message_field: str = "message",
    date_field: str = "timestamp",
) -> dict[str, Any]:
    """
    Summarize errors across a batch of records, at aggregate level only
    (no record-by-record dump - that stays exclusive to chat mode).

    Returns:
        {
            "total": int,                              # after dedup
            "by_module": {module: count, ...},
            "recurring": [{"module", "message", "count"}, ...],  # count > 1
        }
    """
    error_records = [record for record in records if _is_error_record(record)]
    deduped = _dedupe_error_records(error_records, message_field, date_field)

    by_module: Counter = Counter()
    by_module_and_message: Counter = Counter()

    for record in deduped:
        module = record.get("module") or UNKNOWN_MODULE
        by_module[module] += 1
        by_module_and_message[(module, record.get(message_field))] += 1

    recurring = [
        {"module": module, "message": message, "count": count}
        for (module, message), count in by_module_and_message.items()
        if count > 1
    ]

    return {
        "total": len(deduped),
        "by_module": dict(by_module),
        "recurring": recurring,
    }
