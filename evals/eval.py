"""
Evals harness for analyze_node (Story SCRUM-184).

Runs a fixed set of scenario fixtures (evals/fixtures/*.records.json)
through the report graph (agent.graph.build_graph), scoring each run
against known expectations (evals/fixtures/*.expected.json), and
appends one row per fixture to a timestamped CSV under evals/results/.

By default `analyze_fn` is stubbed to return each fixture's
pre-captured evals/fixtures/*.recorded_response.json - deterministic,
free of API cost, and safe to run in CI. Pass --live to make a real
ChatOpenAI call per fixture instead (periodic real-cost/quality
sampling) - this is a manual/scheduled operation, not part of the
pytest suite (tests/test_evals.py only exercises the harness mechanics
themselves, with tiny inline fixtures and a fake LLM).

Usage
-----
    python -m evals.eval              # stubbed, deterministic, free
    python -m evals.eval --live       # real ChatOpenAI calls, real cost
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agent.graph import build_graph
from agent.nodes.analyze import analyze_records

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"

CSV_FIELDNAMES = [
    "fixture_name", "passed", "attempts", "tokens_in", "tokens_out", "cost_usd", "latency_ms", "notes",
]

# Rough per-1K-token $ rates, used only for --live cost estimation.
MODEL_COST_PER_1K_TOKENS = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}
LIVE_MODEL = "gpt-4o-mini"


class _UsageTrackingLLM:
    """Wraps a real LLM, accumulating token usage across every .invoke() call (including evaluator retries)."""

    def __init__(self, llm):
        self._llm = llm
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def invoke(self, prompt):
        response = self._llm.invoke(prompt)
        usage = getattr(response, "usage_metadata", None) or {}
        self.total_input_tokens += usage.get("input_tokens", 0)
        self.total_output_tokens += usage.get("output_tokens", 0)
        return response


def discover_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> list[str]:
    """Every <scenario> name that has a matching *.records.json file."""
    return sorted(p.stem.removesuffix(".records") for p in fixtures_dir.glob("*.records.json"))


def load_fixture(name: str, fixtures_dir: Path = FIXTURES_DIR) -> tuple[list[dict], dict, dict]:
    """
    Load one fixture's three files. Records are stored as JSON with ISO
    date strings (JSON has no native datetime) - `timestamp` fields are
    converted back to real datetime objects here, since every node
    downstream (stats/errors/duplicates) expects that.
    """
    records = json.loads((fixtures_dir / f"{name}.records.json").read_text(encoding="utf-8"))
    expected = json.loads((fixtures_dir / f"{name}.expected.json").read_text(encoding="utf-8"))
    recorded_response = json.loads((fixtures_dir / f"{name}.recorded_response.json").read_text(encoding="utf-8"))

    for record in records:
        if "timestamp" in record:
            record["timestamp"] = datetime.fromisoformat(record["timestamp"])

    return records, expected, recorded_response


def score_run(result: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Compare one graph invocation's final state against one fixture's
    expectations. Returns (passed, notes) - notes explain every
    mismatch found, not just the first one, so a failing eval run is
    actionable from the CSV alone.
    """
    notes: list[str] = []

    expected_counts = expected.get("expected_counts")
    if expected_counts is not None:
        for category, count in expected_counts.items():
            actual = result["counts"].get(category)
            if actual != count:
                notes.append(f"counts[{category}] expected {count}, got {actual}")

    expected_duplicates = expected.get("expected_duplicates")
    if expected_duplicates is not None:
        actual_duplicates = len(result["anomalies"]["duplicates"])
        if actual_duplicates != expected_duplicates:
            notes.append(f"duplicates expected {expected_duplicates}, got {actual_duplicates}")

    expected_guardrail = expected.get("expected_guardrail_triggered")
    if expected_guardrail is not None:
        actual_guardrail = bool(result.get("guardrail_flags"))
        if actual_guardrail != expected_guardrail:
            notes.append(f"guardrail_triggered expected {expected_guardrail}, got {actual_guardrail}")

    expected_keywords = expected.get("expected_analysis_keywords") or []
    analysis_text = json.dumps(result.get("analysis") or {}).lower()
    for keyword in expected_keywords:
        if keyword.lower() not in analysis_text:
            notes.append(f"analysis missing expected keyword '{keyword}'")

    expect_evaluator_pass = expected.get("expect_evaluator_pass")
    if expect_evaluator_pass is not None and result.get("analysis_ok") != expect_evaluator_pass:
        notes.append(f"analysis_ok expected {expect_evaluator_pass}, got {result.get('analysis_ok')}")

    return (len(notes) == 0), notes


def run_fixture(name: str, live: bool = False, fixtures_dir: Path = FIXTURES_DIR) -> dict[str, Any]:
    """Run one fixture through the report graph and score the result."""
    records, expected, recorded_response = load_fixture(name, fixtures_dir)

    tracking_llm: Optional[_UsageTrackingLLM] = None

    if live:
        from agent.llm import get_chat_openai

        tracking_llm = _UsageTrackingLLM(get_chat_openai(LIVE_MODEL))

        def analyze_fn(counts, errors, anomalies, samples):
            return analyze_records(counts, errors, anomalies, samples, llm=tracking_llm)
    else:
        def analyze_fn(counts, errors, anomalies, samples):
            return recorded_response

    app = build_graph(fetch_fn=lambda start_date, end_date: records, analyze_fn=analyze_fn)

    timestamps = [r["timestamp"] for r in records if "timestamp" in r]
    start_date = min(timestamps) if timestamps else datetime(2026, 1, 1)
    end_date = max(timestamps) if timestamps else datetime(2026, 1, 1)

    start_time = time.monotonic()
    result = app.invoke({"start_date": start_date, "end_date": end_date})
    latency_ms = (time.monotonic() - start_time) * 1000

    passed, notes = score_run(result, expected)

    tokens_in = tracking_llm.total_input_tokens if tracking_llm else 0
    tokens_out = tracking_llm.total_output_tokens if tracking_llm else 0
    cost_rates = MODEL_COST_PER_1K_TOKENS.get(LIVE_MODEL, {"input": 0.0, "output": 0.0})
    cost_usd = (tokens_in / 1000) * cost_rates["input"] + (tokens_out / 1000) * cost_rates["output"]

    return {
        "fixture_name": name,
        "passed": passed,
        "attempts": result.get("analysis_attempts", 0),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 6),
        "latency_ms": round(latency_ms, 1),
        "notes": "; ".join(notes),
    }


def write_results_csv(rows: list[dict[str, Any]], results_dir: Path = RESULTS_DIR, timestamp: Optional[str] = None) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = results_dir / f"eval_{ts}.csv"

    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run analyze_node evals against fixed fixtures.")
    parser.add_argument(
        "--live", action="store_true", help="Call a real ChatOpenAI instead of using recorded_response.json."
    )
    args = parser.parse_args(argv)

    if args.live:
        from dotenv import load_dotenv

        load_dotenv()

    fixture_names = discover_fixtures()
    if not fixture_names:
        print(f"No fixtures found under {FIXTURES_DIR}")
        return 1

    rows = [run_fixture(name, live=args.live) for name in fixture_names]
    results_path = write_results_csv(rows)

    for row in rows:
        status = "PASS" if row["passed"] else "FAIL"
        print(f"[{status}] {row['fixture_name']} ({row['latency_ms']}ms, {row['attempts']} attempt(s))")
        if row["notes"]:
            print(f"    {row['notes']}")

    print(f"\nResults written to {results_path}")

    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
