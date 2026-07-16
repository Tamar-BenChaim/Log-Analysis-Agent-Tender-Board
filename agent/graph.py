"""
LangGraph wiring for the report graph (originally Story SCRUM-39
orchestration; relocated as part of SCRUM-170's restructure).

This file has ONE job today: define the graph that connects the pieces
in agent/nodes/ into a single, runnable pipeline:

    START -> fetch -> (conditional) -> classify -> report -> END
                              \\_____________________________/
                               (on a fetch error, skip straight to report)

The chat graph (agent.graph.build_chat_graph) is added separately in
SCRUM-174 - it does not share state or nodes with this one.

Dependency injection for testability
-------------------------------------
build_graph() takes `fetch_fn` and `count_fn` as parameters (defaulting to
the real MongoDB-backed and rule-based implementations). Tests pass in
fakes, so the whole graph - including the conditional routing - can be
exercised without ever touching a real MongoDB connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.nodes.classify import count_tender_events
from agent.nodes.fetch import fetch_tender_board_activity_logs
from agent.nodes.report import format_error_report, format_report


class ReportState(TypedDict):
    """
    The single shared state object that flows through every node.

    Each node function receives the *entire* current state and returns a
    dict with only the keys it wants to update - LangGraph merges that
    into the state automatically before calling the next node (this is
    the default "overwrite the listed keys" behavior for a plain
    TypedDict state with no Annotated reducers).
    """

    start_date: datetime
    end_date: datetime
    records: list[dict[str, Any]]
    counts: dict[str, int]
    error: Optional[str]
    report: str


FetchFn = Callable[..., list[dict[str, Any]]]
CountFn = Callable[[list[dict[str, Any]]], dict[str, int]]


def build_graph(
    fetch_fn: FetchFn = fetch_tender_board_activity_logs,
    count_fn: CountFn = count_tender_events,
):
    """
    Construct and compile the fetch -> classify -> report pipeline.

    Returns a compiled LangGraph app with a single `.invoke(state)` entry
    point - callers (cli.py, tests) never interact with the individual
    node functions directly.
    """

    def fetch_node(state: ReportState) -> dict[str, Any]:
        """
        Calls the MongoDB fetch function built earlier.

        get_mongo_client()/fetch_tender_board_activity_logs() raise
        RuntimeError on a bad connection - that is caught HERE, not
        allowed to propagate, so the graph can route to a graceful
        error report instead of crashing the whole CLI run.
        """
        try:
            records = fetch_fn(start_date=state["start_date"], end_date=state["end_date"])
            return {"records": records, "error": None}
        except RuntimeError as exc:
            return {"records": [], "error": str(exc)}

    def classify_node(state: ReportState) -> dict[str, Any]:
        """Only reached when fetch_node succeeded."""
        counts = count_fn(state["records"])
        return {"counts": counts}

    def report_node(state: ReportState) -> dict[str, Any]:
        """
        Builds the final printable string.

        Two possible paths lead here: the normal success path (state
        has "counts") and the error shortcut (state has "error" set,
        "counts" was never computed).
        """
        if state.get("error"):
            report = format_error_report(state["start_date"], state["end_date"], state["error"])
        else:
            report = format_report(state["start_date"], state["end_date"], state["counts"])
        return {"report": report}

    def route_after_fetch(state: ReportState) -> str:
        """
        Conditional edge function - LangGraph calls this after fetch_node
        finishes, with the up-to-date state, and expects back the NAME of
        the next node to run.
        """
        return "report" if state.get("error") else "classify"

    builder = StateGraph(ReportState)

    # add_node(name, function): registers each step under a string name
    # that add_edge/add_conditional_edges then refer to.
    builder.add_node("fetch", fetch_node)
    builder.add_node("classify", classify_node)
    builder.add_node("report", report_node)

    # add_edge(START, "fetch"): every run of the graph begins at "fetch".
    builder.add_edge(START, "fetch")

    # add_conditional_edges(source, router, path_map):
    #   after "fetch" runs, call route_after_fetch(state) - whatever
    #   string it returns is looked up in path_map to find the real next
    #   node. This is what lets one node have TWO possible successors
    #   depending on runtime data, instead of always going to a fixed
    #   next node like add_edge does.
    builder.add_conditional_edges(
        "fetch",
        route_after_fetch,
        {"classify": "classify", "report": "report"},
    )

    # Normal path: classify always leads to report.
    builder.add_edge("classify", "report")

    # Every path ends the same way: report -> END.
    builder.add_edge("report", END)

    # compile() validates the graph (e.g. every node reachable, START and
    # END wired correctly) and returns a runnable object.
    return builder.compile()


if __name__ == "__main__":
    # Manual smoke-test entry point for local development only.
    # The real CLI (argument parsing) is agent/cli.py.
    from dotenv import load_dotenv

    load_dotenv()

    app = build_graph()
    result = app.invoke(
        {
            "start_date": datetime(2026, 1, 1),
            "end_date": datetime(2026, 12, 31),
        }
    )
    print(result["report"])
