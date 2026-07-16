"""Unit tests for agent.nodes.report (originally part of Story SCRUM-39)."""

from datetime import datetime

from agent.nodes.classify import ALL_CATEGORIES
from agent.nodes.report import format_error_report, format_report


def _zero_counts() -> dict:
    return {category: 0 for category in ALL_CATEGORIES}


def test_format_report_shows_every_business_category_and_total():
    counts = _zero_counts()
    counts.update({"create": 2, "register": 1, "edit": 3, "delete": 0, "view": 10, "other": 4, "invalid": 1})

    report = format_report(datetime(2026, 1, 1), datetime(2026, 1, 31), counts)

    assert "CREATE" in report
    assert "REGISTER" in report
    assert "EDIT" in report
    assert "DELETE" in report
    assert "VIEW" in report
    assert "OTHER" in report
    assert "INVALID" in report
    # 2 + 1 + 3 + 0 + 10 + 4 + 1 = 21
    assert "TOTAL" in report
    assert "21" in report


def test_format_report_includes_the_date_range():
    report = format_report(datetime(2026, 3, 1), datetime(2026, 3, 31), _zero_counts())

    assert "2026-03-01" in report
    assert "2026-03-31" in report


def test_format_error_report_mentions_the_error_message():
    report = format_error_report(datetime(2026, 1, 1), datetime(2026, 1, 31), "Could not connect to MongoDB: timeout")

    assert "ERROR" in report
    assert "Could not connect to MongoDB: timeout" in report


# --- error_summary / anomalies sections (SCRUM-166) ---------------------


def test_format_report_without_error_summary_or_anomalies_is_unchanged():
    # Existing zero-arg call sites must keep working exactly as before.
    report = format_report(datetime(2026, 1, 1), datetime(2026, 1, 31), _zero_counts())

    assert "Errors" not in report
    assert "Anomalies" not in report


def test_format_report_includes_error_summary_section():
    error_summary = {
        "total": 2,
        "by_module": {"tenderBoard": 2},
        "recurring": [{"module": "tenderBoard", "message": "DB timeout", "count": 2}],
    }

    report = format_report(
        datetime(2026, 1, 1), datetime(2026, 1, 31), _zero_counts(), error_summary=error_summary
    )

    assert "Errors" in report
    assert "tenderBoard: 2" in report
    assert "RECURRING x2" in report
    assert "DB timeout" in report


def test_format_report_includes_anomalies_section():
    anomalies = {
        "duplicates": [
            {
                "user_id": "user-1",
                "organization_id": "org-1",
                "tender_ids": ["a", "b"],
                "request_ids": ["r1", "r2"],
                "seconds_apart": 0.6,
            }
        ],
        "slow_requests": [{"message": "POST /tender-board/smart-create 201 2382ms", "duration_ms": 2382}],
    }

    report = format_report(datetime(2026, 1, 1), datetime(2026, 1, 31), _zero_counts(), anomalies=anomalies)

    assert "Anomalies" in report
    assert "1 duplicate-submit cluster(s) detected" in report
    assert "user-1" in report
    assert "1 request(s) exceeded the latency threshold" in report
    assert "2382ms" in report


def test_format_report_truncates_long_slow_request_messages():
    long_query = "q=" + ("a" * 200)
    anomalies = {
        "duplicates": [],
        "slow_requests": [
            {"message": f"GET /tender-board/smart-search?{long_query} 200 3579ms", "duration_ms": 3579}
        ],
    }

    report = format_report(datetime(2026, 1, 1), datetime(2026, 1, 31), _zero_counts(), anomalies=anomalies)

    assert "..." in report
    assert long_query not in report
    assert "200 3579ms" in report
