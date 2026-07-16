"""Unit tests for agent.nodes.errors (Story SCRUM-166)."""

from datetime import datetime

from agent.nodes.errors import summarize_errors


def test_summarize_errors_counts_by_module():
    records = [
        {"level": "error", "module": "tenderBoard", "message": "Apply to tender failed", "requestId": "r1"},
        {"level": "error", "module": "auth", "message": "Invalid token", "requestId": "r2"},
    ]

    summary = summarize_errors(records)

    assert summary["total"] == 2
    assert summary["by_module"] == {"tenderBoard": 1, "auth": 1}
    assert summary["recurring"] == []


def test_summarize_errors_ignores_non_error_records():
    records = [
        {"level": "info", "module": "tenderBoard", "message": "Tender created successfully"},
    ]

    summary = summarize_errors(records)

    assert summary["total"] == 0
    assert summary["by_module"] == {}


def test_summarize_errors_dedupes_same_error_logged_twice_with_same_request_id():
    # generateTenderData logs the error, re-throws, createSmartTender
    # logs the SAME error again - both share one requestId.
    records = [
        {
            "level": "error",
            "module": "tenderBoard",
            "message": "AI generation failed: timeout",
            "requestId": "req-123",
        },
        {
            "level": "error",
            "module": "tenderBoard",
            "message": "AI generation failed: timeout",
            "requestId": "req-123",
        },
    ]

    summary = summarize_errors(records)

    # One real failure, not two.
    assert summary["total"] == 1
    assert summary["by_module"] == {"tenderBoard": 1}


def test_summarize_errors_recurring_detected_across_different_requests():
    records = [
        {"level": "error", "module": "tenderBoard", "message": "DB timeout", "requestId": "req-1"},
        {"level": "error", "module": "tenderBoard", "message": "DB timeout", "requestId": "req-2"},
        {"level": "error", "module": "tenderBoard", "message": "DB timeout", "requestId": "req-3"},
    ]

    summary = summarize_errors(records)

    assert summary["total"] == 3
    assert summary["recurring"] == [{"module": "tenderBoard", "message": "DB timeout", "count": 3}]


def test_summarize_errors_missing_module_falls_back_to_unknown():
    records = [{"level": "error", "message": "Something broke", "requestId": "req-1"}]

    summary = summarize_errors(records)

    assert summary["by_module"] == {"unknown": 1}


def test_summarize_errors_dedupe_fallback_without_request_id_uses_module_message_timestamp():
    ts = datetime(2026, 1, 1, 12, 0, 0, 500000)
    ts_same_second = datetime(2026, 1, 1, 12, 0, 0, 900000)
    records = [
        {"level": "error", "module": "tenderBoard", "message": "Legacy failure", "timestamp": ts},
        {"level": "error", "module": "tenderBoard", "message": "Legacy failure", "timestamp": ts_same_second},
    ]

    summary = summarize_errors(records)

    # Both records lack requestId (old-format logs) and round to the
    # same second -> treated as one duplicate-logged failure.
    assert summary["total"] == 1


def test_summarize_errors_stack_without_level_error_still_counts():
    records = [{"module": "tenderBoard", "message": "Unhandled", "stack": "Error: boom", "requestId": "r1"}]

    summary = summarize_errors(records)

    assert summary["total"] == 1


def test_summarize_errors_on_empty_list():
    summary = summarize_errors([])
    assert summary == {"total": 0, "by_module": {}, "recurring": []}
