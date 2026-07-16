"""
Shared free-text prompt-injection screening (Story SCRUM-174, reused by
analyze_node in the report graph - Story SCRUM-180).

screen_free_text() is the ONE implementation used by both:
  - the chat graph's tool_node (this story): every tool-result text
    field (tender titles, additionalDetails, error messages) passes
    through this before agent_node ever sees it. A tool result is just
    as attacker-controlled as a typed user question - additionalDetails
    is free text an end user entered on the Tender Board site itself,
    stored verbatim in Mongo, and returned unmodified by tools like
    get_request_trace.
  - analyze_node in the report graph (SCRUM-180): the same guarantee
    for the small sample of real content it feeds to one LLM call.

This module has no knowledge of ReportState or ChatState - it is a
pure text -> text function, callable from either graph's nodes without
either one depending on the other's state shape.
"""

from __future__ import annotations

import re
from typing import Any

from agent.nodes.classify import _is_error_record

MAX_SAMPLE_LENGTH = 500
MAX_SAMPLES = 8
REDACTED_MARKER = "[REDACTED - potential prompt injection]"

# Case-insensitive phrasings that commonly indicate a prompt-injection
# attempt embedded in log content, rather than genuine business text
# (a real tender title/description/error message has no reason to
# contain any of these).
_DENYLIST_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore (all |any )?(previous|prior|the above)",
        r"disregard (all |any )?(previous|prior)",
        r"you are now",
        r"system prompt",
        r"new instructions\s*:",
    ]
]

# Control characters (excluding \t\n\r, which are harmless whitespace)
# stripped unconditionally - they have no place in a tender title or
# error message and can be used to obscure denylist matches.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def screen_free_text(text: str) -> tuple[str, list[str]]:
    """
    Screen one piece of free text before it can reach an LLM prompt.

    Returns (clean_text, flags):
      - flags is a list of short reason strings, empty when nothing was
        found.
      - On a denylist match, the ENTIRE sample is replaced with
        REDACTED_MARKER - not just the matched substring - since the
        surrounding text could still carry payload in other words;
        partial redaction isn't a safe guarantee.
      - Control characters are always stripped and the result is
        always hard-truncated to MAX_SAMPLE_LENGTH, whether or not
        anything matched - an over-long sample is a cost/context
        problem regardless of intent.

    Never raises: a non-string input returns ("", []) rather than
    crashing the caller.
    """
    if not isinstance(text, str):
        return "", []

    cleaned = _CONTROL_CHAR_PATTERN.sub("", text)

    flags = [
        f"prompt_injection_denylist:{pattern.pattern}"
        for pattern in _DENYLIST_PATTERNS
        if pattern.search(cleaned)
    ]

    if flags:
        return REDACTED_MARKER, flags

    return cleaned[:MAX_SAMPLE_LENGTH], []


def collect_and_screen_samples(
    records: list[dict[str, Any]], max_samples: int = MAX_SAMPLES
) -> tuple[list[str], list[str]]:
    """
    Pull a small sample of real free-text business content out of a
    batch of records - tender titles/descriptions and error messages -
    and screen every one of them through screen_free_text before
    analyze_node's prompt is built (Story SCRUM-180).

    Returns (samples, flags): `samples` is already safe to interpolate
    into an LLM prompt (redacted where needed); `flags` is the combined
    list of every reason any sample was redacted, for guardrail_node to
    surface in the report (never repeating the injected payload itself,
    just that something was caught).

    Deliberately looks at title/shortDescription (both free text an end
    user typed on the Tender Board site, same as additionalDetails) in
    addition to error messages - not just the one field mentioned as an
    example in analyze_node's design.
    """
    raw_samples: list[str] = []
    for record in records:
        context = record.get("context")
        if isinstance(context, dict):
            tender = context.get("tender")
            if isinstance(tender, dict):
                for field in ("title", "shortDescription"):
                    value = tender.get(field)
                    if isinstance(value, str) and value:
                        raw_samples.append(value)

        if _is_error_record(record):
            message = record.get("message")
            if isinstance(message, str) and message:
                raw_samples.append(message)

    samples: list[str] = []
    flags: list[str] = []
    for raw in raw_samples:
        if len(samples) >= max_samples:
            break
        clean, sample_flags = screen_free_text(raw)
        samples.append(clean)
        flags.extend(sample_flags)

    return samples, flags
