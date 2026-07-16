"""
LangGraph wiring for the report graph (originally Story SCRUM-39
orchestration; relocated as part of SCRUM-170's restructure; extended
with stats/errors in SCRUM-166).

This file has ONE job today: define the graph that connects the pieces
in agent/nodes/ into a single, runnable pipeline:

    START -> fetch -> (conditional) -> classify -> stats -> errors -> report -> END
                              \\____________________________________________/
                               (on a fetch error, skip straight to report)

classify/stats/errors run SEQUENTIALLY here, one after another - not
yet the parallel fan-out shown in the design doc. That fan-out (plus
aggregate/guardrail/analyze/evaluator) is SCRUM-180's job; wiring the
graph shape twice for the same three nodes would be wasted churn.

The chat graph (agent.graph.build_chat_graph, SCRUM-174) is defined
further down this file - it is a completely separate graph, with its
own state (ChatState) and no shared nodes with the report graph above.
It only runs in `chat` mode.

Dependency injection for testability
-------------------------------------
build_graph() takes `fetch_fn`/`count_fn`/`stats_fn`/`errors_fn` as
parameters (defaulting to the real implementations). Tests pass in
fakes, so the whole graph - including the conditional routing - can be
exercised without ever touching a real MongoDB connection. build_chat_graph()
takes an `llm` parameter for the same reason - a fake LLM lets tests
exercise the ReAct loop without ever calling a real model.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Callable, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.nodes.classify import count_tender_events
from agent.nodes.errors import summarize_errors
from agent.nodes.fetch import fetch_tender_board_activity_logs
from agent.nodes.guardrail import screen_free_text
from agent.nodes.report import format_error_report, format_report
from agent.nodes.stats import compute_latency_stats, find_duplicate_tenders
from agent.tools import SIDE_EFFECT_TOOLS, TOOLS


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
    stats: dict[str, Any]
    errors: dict[str, Any]
    anomalies: dict[str, Any]
    report: str


FetchFn = Callable[..., list[dict[str, Any]]]
CountFn = Callable[[list[dict[str, Any]]], dict[str, int]]
StatsFn = Callable[..., dict[str, Any]]
ErrorsFn = Callable[..., dict[str, Any]]


def build_graph(
    fetch_fn: FetchFn = fetch_tender_board_activity_logs,
    count_fn: CountFn = count_tender_events,
    duplicates_fn: StatsFn = find_duplicate_tenders,
    latency_fn: StatsFn = compute_latency_stats,
    errors_fn: ErrorsFn = summarize_errors,
):
    """
    Construct and compile the fetch -> classify -> stats -> errors ->
    report pipeline.

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

    def stats_node(state: ReportState) -> dict[str, Any]:
        """Latency stats + duplicate-tender detection over the same batch."""
        duplicates = duplicates_fn(state["records"])
        latency = latency_fn(state["records"])
        return {
            "stats": {"latency": latency},
            "anomalies": {
                "duplicates": duplicates,
                "slow_requests": latency["over_threshold"],
            },
        }

    def errors_node(state: ReportState) -> dict[str, Any]:
        """Aggregate error summary (deduped, grouped by module)."""
        return {"errors": errors_fn(state["records"])}

    def report_node(state: ReportState) -> dict[str, Any]:
        """
        Builds the final printable string.

        Two possible paths lead here: the normal success path (state
        has "counts"/"errors"/"anomalies") and the error shortcut (state
        has "error" set, nothing downstream of fetch ever ran).
        """
        if state.get("error"):
            report = format_error_report(state["start_date"], state["end_date"], state["error"])
        else:
            report = format_report(
                state["start_date"],
                state["end_date"],
                state["counts"],
                error_summary=state["errors"],
                anomalies=state["anomalies"],
            )
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
    builder.add_node("stats", stats_node)
    builder.add_node("errors", errors_node)
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

    # Normal path: classify -> stats -> errors -> report, sequentially.
    builder.add_edge("classify", "stats")
    builder.add_edge("stats", "errors")
    builder.add_edge("errors", "report")

    # Every path ends the same way: report -> END.
    builder.add_edge("report", END)

    # compile() validates the graph (e.g. every node reachable, START and
    # END wired correctly) and returns a runnable object.
    return builder.compile()


class ChatState(TypedDict):
    """
    Shared state for the chat graph. `messages` uses the add_messages
    reducer so each turn's new message(s) are appended to history
    rather than overwriting it (the default TypedDict merge behavior
    ReportState relies on above would instead replace the whole list).

    `guardrail_flags` accumulates across the whole conversation (every
    tool call that triggers a flag adds to it, never replaces it) via
    the operator.add reducer - so a chat session's flags aren't lost
    turn to turn.
    """

    messages: Annotated[list, add_messages]
    guardrail_flags: Annotated[list[str], operator.add]


def _has_tool_calls(message: Any) -> bool:
    return bool(getattr(message, "tool_calls", None))


def build_chat_graph(llm: Any = None, tools: Optional[list] = None):
    """
    Construct and compile the interactive chat graph:

        START -> agent -> (tool_calls?) -> hitl/tool -> agent -> ... -> END
                        \\_____________(no tool_calls)____________/

    Classic ReAct loop: agent_node decides whether to call a tool or
    give a final answer; tool_node runs read-only tools immediately;
    hitl_node is scaffolding for a future write-tool (SIDE_EFFECT_TOOLS
    is empty today, so hitl_node is reachable but never actually pauses
    anything - see its docstring).

    `llm` is injectable for tests: pass a fake object exposing
    `.invoke(messages) -> AIMessage` (already "bound" to whatever
    canned tool-call sequence the test wants) to exercise the loop
    without ever calling a real model. Production code leaves it out
    and gets a real ChatOpenAI bound to `tools`.

    `tools` is injectable too, defaulting to the real six chat tools
    (agent.tools.TOOLS) - tests pass a couple of trivial fake tools
    instead, so exercising the loop never touches real MongoDB, on top
    of never calling a real model.
    """
    resolved_tools = tools if tools is not None else TOOLS
    llm_with_tools = llm if llm is not None else ChatOpenAI(model="gpt-4o-mini").bind_tools(resolved_tools)
    base_tool_node = ToolNode(resolved_tools)

    def agent_node(state: ChatState) -> dict[str, Any]:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def hitl_node(state: ChatState) -> dict[str, Any]:
        """
        Scaffolding only. SIDE_EFFECT_TOOLS (agent/tools.py) is empty
        today - every tool this story ships is read-only - so
        route_after_agent below never actually routes here yet. Once a
        write-tool exists, adding its name to SIDE_EFFECT_TOOLS is
        enough to make route_after_agent send it here instead of
        straight to "tool"; approval/pause logic belongs in this
        function when that day comes.
        """
        return {}

    def tool_node(state: ChatState) -> dict[str, Any]:
        """
        Runs the pending tool call(s), then screens every text tool
        result through the same screen_free_text used by analyze_node
        (SCRUM-180) - a tool result is exactly as attacker-controlled
        as a typed user question (e.g. get_request_trace can surface
        context.tender.additionalDetails, free text an end user typed
        on the Tender Board site). This is a second, independent check
        on top of the screening tools already do internally (e.g.
        get_request_trace) - defense in depth against a future tool
        that forgets to self-screen.
        """
        result = base_tool_node.invoke(state)
        screened_messages = []
        flags: list[str] = []
        for message in result["messages"]:
            content = message.content
            if isinstance(content, str):
                clean, message_flags = screen_free_text(content)
                if message_flags:
                    message = message.model_copy(update={"content": clean})
                    flags.extend(message_flags)
            screened_messages.append(message)

        update: dict[str, Any] = {"messages": screened_messages}
        if flags:
            update["guardrail_flags"] = flags
        return update

    def route_after_agent(state: ChatState) -> str:
        last_message = state["messages"][-1]
        if not _has_tool_calls(last_message):
            return "end"
        if any(call["name"] in SIDE_EFFECT_TOOLS for call in last_message.tool_calls):
            return "hitl"
        return "tool"

    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    builder.add_node("hitl", hitl_node)
    builder.add_node("tool", tool_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {"end": END, "hitl": "hitl", "tool": "tool"},
    )
    builder.add_edge("hitl", "tool")
    builder.add_edge("tool", "agent")

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
