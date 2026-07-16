"""Unit tests for agent.nodes.stats (Story SCRUM-166)."""

from datetime import datetime, timedelta

from agent.nodes.stats import compute_latency_stats, find_duplicate_tenders


def _tender_record(user_id, org_id, tender_id, request_id, timestamp, title="Roof repair", budget=1000):
    return {
        "userId": user_id,
        "organizationId": org_id,
        "requestId": request_id,
        "timestamp": timestamp,
        "message": "Tender created successfully",
        "context": {"tenderId": tender_id, "tender": {"title": title, "budget": budget}},
    }


# --- find_duplicate_tenders ----------------------------------------------


def test_duplicate_detected_within_window():
    base = datetime(2026, 1, 1, 12, 0, 0)
    records = [
        _tender_record("user-1", "org-1", "tender-a", "req-a", base),
        _tender_record("user-1", "org-1", "tender-b", "req-b", base + timedelta(milliseconds=600)),
    ]

    duplicates = find_duplicate_tenders(records)

    assert len(duplicates) == 1
    dup = duplicates[0]
    assert dup["user_id"] == "user-1"
    assert dup["organization_id"] == "org-1"
    assert set(dup["tender_ids"]) == {"tender-a", "tender-b"}
    assert dup["seconds_apart"] == 0.6


def test_no_duplicate_when_outside_window():
    base = datetime(2026, 1, 1, 12, 0, 0)
    records = [
        _tender_record("user-1", "org-1", "tender-a", "req-a", base),
        _tender_record("user-1", "org-1", "tender-b", "req-b", base + timedelta(seconds=30)),
    ]

    assert find_duplicate_tenders(records) == []


def test_no_duplicate_when_different_user_or_org():
    base = datetime(2026, 1, 1, 12, 0, 0)
    records = [
        _tender_record("user-1", "org-1", "tender-a", "req-a", base),
        _tender_record("user-2", "org-1", "tender-b", "req-b", base + timedelta(milliseconds=200)),
        _tender_record("user-1", "org-2", "tender-c", "req-c", base + timedelta(milliseconds=200)),
    ]

    assert find_duplicate_tenders(records) == []


def test_no_duplicate_when_content_differs():
    base = datetime(2026, 1, 1, 12, 0, 0)
    records = [
        _tender_record("user-1", "org-1", "tender-a", "req-a", base, title="Roof repair"),
        _tender_record(
            "user-1", "org-1", "tender-b", "req-b", base + timedelta(milliseconds=200), title="Plumbing"
        ),
    ]

    assert find_duplicate_tenders(records) == []


def test_records_without_context_are_ignored():
    records = [{"message": "GET /tender-board 200 4ms", "timestamp": datetime(2026, 1, 1)}]
    assert find_duplicate_tenders(records) == []


def test_find_duplicate_tenders_on_empty_list():
    assert find_duplicate_tenders([]) == []


# --- compute_latency_stats ------------------------------------------------


def test_latency_stats_computes_count_avg_max():
    records = [
        {"message": "GET /tender-board 200 100ms"},
        {"message": "GET /tender-board 200 300ms"},
    ]

    stats = compute_latency_stats(records)

    assert stats["count"] == 2
    assert stats["avg_ms"] == 200.0
    assert stats["max_ms"] == 300
    assert stats["over_threshold"] == []


def test_latency_stats_flags_requests_over_threshold():
    records = [
        {"message": "POST /tender-board/smart-create 201 2382ms"},
        {"message": "GET /tender-board 200 50ms"},
    ]

    stats = compute_latency_stats(records, slow_threshold_ms=2000)

    assert stats["count"] == 2
    assert len(stats["over_threshold"]) == 1
    assert stats["over_threshold"][0]["duration_ms"] == 2382


def test_latency_stats_skips_unparseable_records_without_crashing():
    records = [
        {"message": "Tender created successfully"},  # no duration suffix
        {"timestamp": "no message field here"},
        {"message": "GET /tender-board 200 40ms"},
    ]

    stats = compute_latency_stats(records)

    assert stats["count"] == 1
    assert stats["avg_ms"] == 40.0


def test_latency_stats_on_empty_list():
    stats = compute_latency_stats([])
    assert stats == {"count": 0, "avg_ms": 0.0, "max_ms": 0, "over_threshold": []}
