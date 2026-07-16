"""
Classify & Count Tender Events (originally Story SCRUM-38; documented
for the enriched log schema in SCRUM-161).

Input: the raw list[dict] records produced by
agent.nodes.fetch.fetch_tender_board_activity_logs.

Output: for each record, one category label; and a total count per
category across the whole batch.

Why rule-based text matching (not an enum field)
-------------------------------------------------
The source data (test.applicationlogs) is a free-text winston log, not a
structured "event" table - there is no ready-made "action" field to read.
Every record is one of two shapes:

  1. An HTTP access-log line, e.g. "GET /tender-board/product-types 200 4ms"
     - reliable: we can parse the HTTP method + path directly. These
       lines are never enriched with `module`/`context` (they come from
       the morgan HTTP middleware, not application code), so this branch
       is untouched by the enriched-log schema.
  2. A logger.info/warn/error(...) call from tenderBoardService.ts /
     tenderBoardAIService.ts, e.g. "Tender created successfully"
     - less reliable: we match on keywords in the message text.

Enriched-log fields and why classification barely changes because of them
---------------------------------------------------------------------------
agent.nodes.fetch now also returns records that were matched purely via
the new `module == "tenderBoard"` signal (see its module docstring),
even when their `message` does not contain the literal word "tender" -
e.g. "Applicant registered successfully" (no "tender" substring, but
still module="tenderBoard"). classify_log_record already handles this
correctly with NO change needed: unlike fetch's query, it never
required the word "tender" to be present in the message in the first
place - it always ran `_KEYWORD_RULES` keyword matching against
whatever text is there, `module` or no `module`. A record that matches
no keyword rule falls to OTHER regardless of module - a legitimate,
honest outcome for a real business event whose exact wording isn't in
the rule table yet, not a bug. Records with no `module` key at all
(pre-enrichment / old-format logs) are classified through the exact
same code path, which is the backward-compatibility guarantee this
story requires - there is nothing conditional on `module` being
present for this reason. `context` (e.g. {"tenderId": ..., "tender":
{...}}) is intentionally NOT consumed here - it has no bearing on which
of CREATE/REGISTER/EDIT/DELETE/VIEW a record is; it becomes relevant
for duplicate-detection in agent.nodes.stats (SCRUM-166) instead.

Category decisions confirmed with the story owner:
  - "Tender closed successfully" (closeTender) counts as `delete` - the
    underlying update sets isActive=false, but from a usage-pattern
    standpoint it behaves like removing the tender from the active list.
  - A failed operation (e.g. "Apply to tender failed") still counts
    under its normal category (register, in that example) - failures
    are not split into a separate bucket.
  - Internal/system bookkeeping lines that are not a user action on the
    board at all (e.g. "Starting AI tender data generation",
    "Backfilling embeddings for N tenders") fall through to `other`.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

# The five business categories the story asks for.
CREATE = "create"
REGISTER = "register"
EDIT = "edit"
DELETE = "delete"
VIEW = "view"

# Two extra buckets needed to make the count *complete* and *honest*:
OTHER = "other"      # well-formed message, but not a recognized tender action
INVALID = "invalid"  # the record itself is malformed (missing/bad message field)

ALL_CATEGORIES = [CREATE, REGISTER, EDIT, DELETE, VIEW, OTHER, INVALID]

# Matches the start of an HTTP access-log line, e.g.:
#   "GET /tender-board/product-types 200 4ms"
#   "POST /tender-board/6a4fb.../apply 200 815ms"
# Group 1 = HTTP method, group 2 = path (stops at the next whitespace).
_HTTP_LOG_PATTERN = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)", re.IGNORECASE)


def _classify_http_line(method: str, path: str) -> str:
    """Classify an HTTP access-log line by method + path."""
    method = method.upper()

    if method == "DELETE":
        return DELETE
    if method in ("PUT", "PATCH"):
        return EDIT
    if method == "GET":
        return VIEW
    if method == "POST":
        # .../apply is the "register as an applicant" endpoint;
        # every other POST under /tender-board (including smart-create)
        # is a creation.
        return REGISTER if "/apply" in path else CREATE

    return OTHER  # unreachable given the regex, kept for safety


# Keyword -> category, checked in order (first match wins). Order matters:
# more specific phrases are listed before more generic ones.
_KEYWORD_RULES: list[tuple[str, str]] = [
    ("tender closed", DELETE),
    ("tender deleted", DELETE),
    ("applicant registered", REGISTER),
    ("application to tender", REGISTER),
    ("apply to tender", REGISTER),
    ("tender created", CREATE),
    ("createsmarttender", CREATE),
    ("tender updated", EDIT),
    ("fetched tender", VIEW),
    ("tender not found", VIEW),
    ("smart search", VIEW),
]


def _classify_message_text(message: str) -> str:
    """Classify a plain logger message by keyword matching."""
    lowered = message.lower()
    for keyword, category in _KEYWORD_RULES:
        if keyword in lowered:
            return category
    return OTHER


def _is_error_record(record: dict[str, Any]) -> bool:
    """
    True if a record represents an error, regardless of its business
    category - failures still count under their normal category per the
    confirmed story decision above (see test_logger_failed_apply_still_counts_as_register).

    This is deliberately NOT used by classify_log_record itself; it
    exists for agent.nodes.errors.summarize_errors (SCRUM-166), which
    needs to identify error records separately from what business
    action they were part of.
    """
    return record.get("level") == "error" or bool(record.get("stack"))


def classify_log_record(record: dict[str, Any], message_field: str = "message") -> str:
    """
    Classify a single log record into one of ALL_CATEGORIES.

    This function never raises - a malformed record (missing message
    field, or a message that isn't a string) is classified as INVALID
    rather than crashing the batch, per the Definition of Done:
    "corrupted/missing record edge cases are handled".
    """
    if not isinstance(record, dict):
        return INVALID

    message: Optional[Any] = record.get(message_field)

    if not isinstance(message, str) or not message.strip():
        return INVALID

    http_match = _HTTP_LOG_PATTERN.match(message.strip())
    if http_match:
        method, path = http_match.groups()
        return _classify_http_line(method, path)

    return _classify_message_text(message)


def count_tender_events(
    records: list[dict[str, Any]], message_field: str = "message"
) -> dict[str, int]:
    """
    Classify every record and return a count per category.

    Every category in ALL_CATEGORIES is always present in the result
    (with 0 if nothing matched it) - this makes downstream code (the
    report formatting in agent.nodes.report) simple, since it never has
    to guard against a missing key.
    """
    counts = Counter(
        classify_log_record(record, message_field=message_field) for record in records
    )
    return {category: counts.get(category, 0) for category in ALL_CATEGORIES}
