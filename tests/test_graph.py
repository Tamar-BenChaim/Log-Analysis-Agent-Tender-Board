"""
Unit tests for agent.graph (originally Story SCRUM-39 orchestration).

These tests never touch MongoDB or LangGraph's default checkpointer -
`fetch_fn` / `count_fn` are replaced with fakes injected via
build_graph(), so we can deterministically test BOTH branches of the
conditional edge (success -> classify -> report, and error -> report).
"""

from datetime import datetime

from agent.graph import build_graph


def test_graph_success_path_runs_fetch_then_classify_then_report():
    calls = {"count_fn_called_with": None}

    def fake_fetch_fn(start_date, end_date):
        return [{"message": "Tender created successfully"}]

    def fake_count_fn(records):
        calls["count_fn_called_with"] = records
        return {"create": 1, "register": 0, "edit": 0, "delete": 0, "view": 0, "other": 0, "invalid": 0}

    app = build_graph(fetch_fn=fake_fetch_fn, count_fn=fake_count_fn)
    result = app.invoke({"start_date": datetime(2026, 1, 1), "end_date": datetime(2026, 1, 31)})

    # classify_node DID run - proven by the fake having been called at all.
    assert calls["count_fn_called_with"] == [{"message": "Tender created successfully"}]
    assert result["error"] is None
    assert "CREATE" in result["report"]
    assert "1" in result["report"]


def test_graph_error_path_skips_classify_and_reports_the_error():
    calls = {"count_fn_was_called": False}

    def failing_fetch_fn(start_date, end_date):
        raise RuntimeError("Could not connect to MongoDB: bad URI")

    def fake_count_fn(records):
        # Should NEVER be reached - the conditional edge must route
        # straight from fetch to report when there's an error.
        calls["count_fn_was_called"] = True
        return {}

    app = build_graph(fetch_fn=failing_fetch_fn, count_fn=fake_count_fn)
    result = app.invoke({"start_date": datetime(2026, 1, 1), "end_date": datetime(2026, 1, 31)})

    assert calls["count_fn_was_called"] is False
    assert result["error"] == "Could not connect to MongoDB: bad URI"
    assert "ERROR" in result["report"]
    assert "Could not connect to MongoDB: bad URI" in result["report"]


def test_graph_handles_zero_records_gracefully():
    app = build_graph(fetch_fn=lambda start_date, end_date: [], count_fn=lambda records: {
        "create": 0, "register": 0, "edit": 0, "delete": 0, "view": 0, "other": 0, "invalid": 0,
    })

    result = app.invoke({"start_date": datetime(2026, 1, 1), "end_date": datetime(2026, 1, 31)})

    assert result["error"] is None
    assert "TOTAL" in result["report"]
    assert result["records"] == []


# --- stats_node / errors_node (SCRUM-166) --------------------------------


def test_graph_runs_stats_and_errors_nodes_and_includes_them_in_the_report():
    def fake_fetch_fn(start_date, end_date):
        return [{"message": "Tender created successfully"}]

    def fake_count_fn(records):
        return {"create": 1, "register": 0, "edit": 0, "delete": 0, "view": 0, "other": 0, "invalid": 0}

    def fake_duplicates_fn(records):
        return [
            {
                "user_id": "user-1",
                "organization_id": "org-1",
                "tender_ids": ["a", "b"],
                "request_ids": ["r1", "r2"],
                "seconds_apart": 0.6,
            }
        ]

    def fake_latency_fn(records):
        return {"count": 1, "avg_ms": 2382.0, "max_ms": 2382, "over_threshold": [{"message": "slow", "duration_ms": 2382}]}

    def fake_errors_fn(records):
        return {"total": 1, "by_module": {"tenderBoard": 1}, "recurring": []}

    app = build_graph(
        fetch_fn=fake_fetch_fn,
        count_fn=fake_count_fn,
        duplicates_fn=fake_duplicates_fn,
        latency_fn=fake_latency_fn,
        errors_fn=fake_errors_fn,
    )
    result = app.invoke({"start_date": datetime(2026, 1, 1), "end_date": datetime(2026, 1, 31)})

    assert result["errors"] == {"total": 1, "by_module": {"tenderBoard": 1}, "recurring": []}
    assert result["anomalies"]["duplicates"] == fake_duplicates_fn(None)
    assert "Errors" in result["report"]
    assert "Anomalies" in result["report"]
    assert "user-1" in result["report"]
