"""
Unit tests for agent.nodes.fetch (originally Story SCRUM-37).

These tests never touch a real MongoDB server - `mongomock` provides an
in-memory fake that behaves like pymongo, so the tests run instantly and
don't need MONGODB_URI to be configured at all.
"""

from datetime import datetime

import mongomock
import pytest

from agent.nodes.fetch import fetch_tender_board_activity_logs


@pytest.fixture
def collection():
    """
    A fresh, empty in-memory collection shaped like the real
    test.applicationlogs collection: shared across the whole backend,
    not just tender-board events.
    """
    client = mongomock.MongoClient()
    return client["test"]["applicationlogs"]


def test_fetch_returns_only_tender_related_records_within_range(collection):
    collection.insert_many(
        [
            # In range AND tender-related - should be returned.
            {"message": "Tender created successfully", "timestamp": datetime(2026, 1, 5)},
            {"message": "GET /tender-board 200 159ms", "timestamp": datetime(2026, 1, 10)},
            # In range but NOT tender-related - must be filtered out.
            {"message": "MongoDB connected successfully", "timestamp": datetime(2026, 1, 6)},
            {"message": "Server running on port 3000", "timestamp": datetime(2026, 1, 7)},
            # Tender-related but OUTSIDE the requested date range.
            {"message": "Tender deleted successfully", "timestamp": datetime(2026, 2, 1)},
        ]
    )

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    assert len(records) == 2
    assert {r["message"] for r in records} == {
        "Tender created successfully",
        "GET /tender-board 200 159ms",
    }


def test_keyword_match_is_case_insensitive(collection):
    collection.insert_one({"message": "TENDER closed successfully", "timestamp": datetime(2026, 1, 5)})

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    assert len(records) == 1


def test_fetch_returns_empty_list_when_no_records_in_range(collection):
    collection.insert_one({"message": "Tender created successfully", "timestamp": datetime(2026, 5, 1)})

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    # Zero records is a normal, valid outcome - not an error.
    assert records == []


def test_fetch_on_completely_empty_collection(collection):
    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 12, 31),
        collection=collection,
    )

    assert records == []


# --- Enriched log schema (SCRUM-161): module-based signal --------------


def test_module_tagged_record_is_fetched_even_without_tender_in_message(collection):
    # New-format business event: no literal "tender" in the message, but
    # module identifies it as tender-board-related. Must be fetched via
    # the new $or signal, not just the old regex.
    collection.insert_one(
        {
            "message": "Applicant approved",
            "timestamp": datetime(2026, 1, 5),
            "module": "tenderBoard",
            "requestId": "req-1",
        }
    )

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    assert len(records) == 1
    assert records[0]["module"] == "tenderBoard"


def test_unrelated_module_without_tender_keyword_is_not_fetched(collection):
    collection.insert_one(
        {
            "message": "User organization updated",
            "timestamp": datetime(2026, 1, 5),
            "module": "organizations",
        }
    )

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    assert records == []


def test_old_format_record_without_module_field_still_fetched_by_keyword(collection):
    # Backward compatibility: pre-enrichment logs have no `module` key at
    # all, and must still be matched by the old regex-on-message signal.
    collection.insert_one(
        {"message": "Tender created successfully", "timestamp": datetime(2026, 1, 5)}
    )

    records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
        collection=collection,
    )

    assert len(records) == 1
    assert "module" not in records[0]
