"""Unit tests for tender_agent.report (part of Story SCRUM-39)."""

from datetime import datetime

from tender_agent.classify import ALL_CATEGORIES
from tender_agent.report import format_error_report, format_report


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
