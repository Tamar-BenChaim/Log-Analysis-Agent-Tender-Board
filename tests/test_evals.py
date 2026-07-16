"""
Unit tests for evals/eval.py's harness mechanics (Story SCRUM-184).

Deliberately narrow in scope: fixture loading, scoring, and CSV
writing, using 1-2 tiny inline fixtures written to a tmp_path - NOT the
real evals/fixtures/ set, and never with --live (no real LLM call
anywhere in this file). Running the full fixture set for real is
`python -m evals.eval`'s job, invoked manually or on a schedule, not
part of the pytest suite.
"""

import csv
import json
from datetime import datetime

from evals.eval import discover_fixtures, load_fixture, run_fixture, score_run, write_results_csv


def _write_fixture(fixtures_dir, name, records, expected, recorded_response):
    (fixtures_dir / f"{name}.records.json").write_text(json.dumps(records), encoding="utf-8")
    (fixtures_dir / f"{name}.expected.json").write_text(json.dumps(expected), encoding="utf-8")
    (fixtures_dir / f"{name}.recorded_response.json").write_text(json.dumps(recorded_response), encoding="utf-8")


# --- discover_fixtures / load_fixture --------------------------------------


def test_discover_fixtures_finds_every_records_json(tmp_path):
    _write_fixture(tmp_path, "alpha", [], {}, {})
    _write_fixture(tmp_path, "beta", [], {}, {})

    assert discover_fixtures(tmp_path) == ["alpha", "beta"]


def test_load_fixture_parses_iso_timestamps_into_datetimes(tmp_path):
    _write_fixture(
        tmp_path,
        "scenario",
        [{"message": "Tender created successfully", "timestamp": "2026-01-05T10:00:00"}],
        {"expected_counts": {"create": 1}},
        {"business_logic_notes": "ok", "error_patterns": [], "anomalies": [], "confidence": 0.9},
    )

    records, expected, recorded_response = load_fixture("scenario", tmp_path)

    assert isinstance(records[0]["timestamp"], datetime)
    assert records[0]["timestamp"] == datetime(2026, 1, 5, 10, 0, 0)
    assert expected["expected_counts"] == {"create": 1}
    assert recorded_response["confidence"] == 0.9


# --- score_run --------------------------------------------------------------


def _fake_result(**overrides):
    base = {
        "counts": {"create": 1, "register": 0, "edit": 0, "delete": 0, "view": 0, "other": 0, "invalid": 0},
        "anomalies": {"duplicates": [], "slow_requests": []},
        "guardrail_flags": [],
        "analysis": {"business_logic_notes": "Looks normal.", "error_patterns": [], "anomalies": [], "confidence": 0.9},
        "analysis_ok": True,
    }
    base.update(overrides)
    return base


def test_score_run_passes_when_everything_matches():
    expected = {
        "expected_counts": {"create": 1},
        "expected_duplicates": 0,
        "expected_guardrail_triggered": False,
        "expected_analysis_keywords": ["normal"],
        "expect_evaluator_pass": True,
    }

    passed, notes = score_run(_fake_result(), expected)

    assert passed is True
    assert notes == []


def test_score_run_fails_and_explains_mismatched_counts():
    expected = {"expected_counts": {"create": 5}}

    passed, notes = score_run(_fake_result(), expected)

    assert passed is False
    assert "counts[create] expected 5, got 1" in notes[0]


def test_score_run_fails_on_missing_expected_keyword():
    expected = {"expected_analysis_keywords": ["duplicate"]}

    passed, notes = score_run(_fake_result(), expected)

    assert passed is False
    assert "duplicate" in notes[0]


def test_score_run_fails_when_evaluator_pass_does_not_match():
    expected = {"expect_evaluator_pass": False}

    passed, notes = score_run(_fake_result(analysis_ok=True), expected)

    assert passed is False


def test_score_run_ignores_unset_expectations():
    # An empty expected dict should never fail anything - only checks
    # explicitly present in `expected` are enforced.
    passed, notes = score_run(_fake_result(), {})

    assert passed is True
    assert notes == []


# --- write_results_csv -------------------------------------------------------


def test_write_results_csv_writes_header_and_rows(tmp_path):
    rows = [
        {
            "fixture_name": "alpha",
            "passed": True,
            "attempts": 1,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": 12.3,
            "notes": "",
        }
    ]

    path = write_results_csv(rows, results_dir=tmp_path, timestamp="20260101_000000")

    assert path.name == "eval_20260101_000000.csv"
    with path.open(encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    assert reader[0]["fixture_name"] == "alpha"
    assert reader[0]["passed"] == "True"


# --- run_fixture (full harness, stubbed analyze_fn, no real LLM/DB) --------


def test_run_fixture_end_to_end_with_stubbed_analyze(tmp_path):
    _write_fixture(
        tmp_path,
        "scenario",
        [{"message": "Tender created successfully", "timestamp": "2026-01-05T10:00:00"}],
        {"expected_counts": {"create": 1}, "expect_evaluator_pass": True},
        {"business_logic_notes": "ok", "error_patterns": [], "anomalies": [], "confidence": 0.9},
    )

    row = run_fixture("scenario", live=False, fixtures_dir=tmp_path)

    assert row["passed"] is True
    assert row["tokens_in"] == 0
    assert row["cost_usd"] == 0.0
    assert row["attempts"] == 1
