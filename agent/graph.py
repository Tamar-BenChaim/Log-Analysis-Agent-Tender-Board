"""
LangGraph wiring for the report graph (originally Story SCRUM-39
orchestration; relocated as part of SCRUM-170's restructure; extended
with stats/errors in SCRUM-166; extended again with the guardrail/
analyze/evaluator "secretary" layer in SCRUM-180).

This file has ONE job today: define the graph that connects the pieces
in agent/nodes/ into a single, runnable pipeline:

    START -> fetch -+-> classify -+
                     +-> stats ---+-> aggregate -> (conditional, error?) -> report -> END
                     +-> errors --+                          |
                                                               +-> guardrail -> analyze -> evaluator -+
                                                                                    ^                  |
                                                                                    +--(fail, attempts < cap)--+
                                                                                    (pass, or cap reached) -> report

fetch fans out to classify/stats/errors via three plain, UNCONDITIONAL
edges (LangGraph's built-in parallel dispatch - no manual async/thread
code needed for multiple edges off one node). This fan-out stays
unconditional even on a fetch error: aggregate_node has three static
incoming edges and LangGraph's join waits for every one of them to
fire in the same step, so conditionally skipping classify would leave
aggregate waiting on a branch that never ran. Instead, each of
classify_node/stats_node/errors_node checks state["error"] itself and
returns {} immediately (still scheduled and "run" by LangGraph, but
does no real work) - aggregate_node then reads that same error flag
and routes straight to report_node, skipping guardrail/analyze/
evaluator entirely. This preserves the original "skip everything
downstream of fetch on error" guarantee while keeping the fan-out
itself simple and unconditional.

The chat graph (agent.graph.build_chat_graph) is defined further down
this file - it is a completely separate graph, with its own state
(ChatState) and no shared nodes with the report graph above. It only
runs in `chat` mode. Every turn first fans out from START to two
independent LLM-based guardrails (topic_guardrail, security_guardrail)
that run concurrently and join at guardrail_gate before agent_node is
ever allowed to run - the same fan-out/join mechanism as fetch's
classify/stats/errors above, just gating entry to the ReAct loop
instead of feeding a report.

Dependency injection for testability
-------------------------------------
build_graph() takes `fetch_fn`/`count_fn`/`stats_fn`/`errors_fn`/
`analyze_fn`/`evaluate_fn` as parameters (defaulting to the real
implementations). Tests pass in fakes, so the whole graph - including
the conditional routing and the analyze/evaluate retry loop - can be
exercised without ever touching a real MongoDB connection or a real
LLM. build_chat_graph() takes `llm`/`topic_guardrail_fn`/
`security_guardrail_fn` parameters for the same reason - fakes let
tests exercise the ReAct loop and the guardrail gate without ever
calling a real model.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Callable, Optional, TypedDict

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.llm import get_chat_openai
from agent.nodes.analyze import analyze_records
from agent.nodes.classify import count_tender_events
from agent.nodes.errors import summarize_errors
from agent.nodes.evaluator import MAX_ANALYSIS_ATTEMPTS, evaluate_analysis
from agent.nodes.fetch import fetch_tender_board_activity_logs
from agent.nodes.guardrail import collect_and_screen_samples, screen_free_text
from agent.nodes.report import format_error_report, format_report
from agent.nodes.security_guardrail import classify_security_risk
from agent.nodes.stats import compute_latency_stats, find_duplicate_tenders
from agent.nodes.topic_guardrail import classify_topic
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
    samples: list[str]
    guardrail_flags: list[str]
    analysis: Optional[dict[str, Any]]
    analysis_attempts: int
    analysis_ok: bool
    report: str


FetchFn = Callable[..., list[dict[str, Any]]]
CountFn = Callable[[list[dict[str, Any]]], dict[str, int]]
StatsFn = Callable[..., dict[str, Any]]
ErrorsFn = Callable[..., dict[str, Any]]
AnalyzeFn = Callable[..., dict[str, Any]]
EvaluateFn = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool]


def build_graph(
    fetch_fn: FetchFn = fetch_tender_board_activity_logs,
    count_fn: CountFn = count_tender_events,
    duplicates_fn: StatsFn = find_duplicate_tenders,
    latency_fn: StatsFn = compute_latency_stats,
    errors_fn: ErrorsFn = summarize_errors,
    analyze_fn: AnalyzeFn = analyze_records,
    evaluate_fn: EvaluateFn = evaluate_analysis,
):
    """
    Construct and compile the fetch -> (classify/stats/errors in
    parallel) -> aggregate -> guardrail -> analyze -> evaluator ->
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
        """Runs in parallel with stats_node/errors_node; no-ops on a fetch error."""
        if state.get("error"):
            return {}
        return {"counts": count_fn(state["records"])}

    def stats_node(state: ReportState) -> dict[str, Any]:
        """Latency stats + duplicate-tender detection; no-ops on a fetch error."""
        if state.get("error"):
            return {}
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
        """Aggregate error summary (deduped, grouped by module); no-ops on a fetch error."""
        if state.get("error"):
            return {}
        return {"errors": errors_fn(state["records"])}

    def aggregate_node(state: ReportState) -> dict[str, Any]:
        """
        The single join point after the classify/stats/errors fan-out -
        LangGraph waits for all three parallel branches to finish
        before running the node with three incoming edges. Nothing to
        merge by hand: each branch already wrote distinct ReportState
        keys (counts / stats+anomalies / errors), so this is a no-op
        that exists purely as that synchronization point.
        """
        return {}

    def guardrail_node(state: ReportState) -> dict[str, Any]:
        """Collects and screens the small content sample analyze_node will see."""
        if state.get("error"):
            return {}
        samples, flags = collect_and_screen_samples(state["records"])
        return {"samples": samples, "guardrail_flags": flags}

    def analyze_node(state: ReportState) -> dict[str, Any]:
        """The one real LLM call. Re-entered on a retry from evaluator_node."""
        analysis = analyze_fn(state["counts"], state["errors"], state["anomalies"], state["samples"])
        attempts = state.get("analysis_attempts", 0) + 1
        return {"analysis": analysis, "analysis_attempts": attempts}

    def evaluator_node(state: ReportState) -> dict[str, Any]:
        """Decides whether analyze_node's output is trustworthy enough to show."""
        ok = evaluate_fn(state["analysis"], state["errors"], state["anomalies"])
        return {"analysis_ok": ok}

    def report_node(state: ReportState) -> dict[str, Any]:
        """
        Builds the final printable string.

        Three possible paths lead here: the error shortcut (state has
        "error" set, nothing downstream of fetch ever ran), the normal
        success path with an approved analysis, and the "analysis never
        passed evaluation within the retry cap" path.
        """
        if state.get("error"):
            report = format_error_report(state["start_date"], state["end_date"], state["error"])
        elif state.get("analysis_ok"):
            report = format_report(
                state["start_date"],
                state["end_date"],
                state["counts"],
                error_summary=state["errors"],
                anomalies=state["anomalies"],
                analysis=state["analysis"],
            )
        else:
            report = format_report(
                state["start_date"],
                state["end_date"],
                state["counts"],
                error_summary=state["errors"],
                anomalies=state["anomalies"],
                analysis_unavailable_reason=(
                    f"evaluator could not validate the analysis after "
                    f"{state.get('analysis_attempts', 0)} attempt(s)"
                ),
            )
        return {"report": report}

    def route_after_aggregate(state: ReportState) -> str:
        """On a fetch error, every parallel branch already no-op'd - skip straight to report."""
        return "report" if state.get("error") else "guardrail"

    def route_after_evaluator(state: ReportState) -> str:
        """Pass -> report. Fail -> retry analyze_node, unless the attempt cap is reached."""
        if state.get("analysis_ok"):
            return "report"
        if state.get("analysis_attempts", 0) >= MAX_ANALYSIS_ATTEMPTS:
            return "report"
        return "analyze"

    builder = StateGraph(ReportState)

    # add_node(name, function): registers each step under a string name
    # that add_edge/add_conditional_edges then refer to.
    builder.add_node("fetch", fetch_node)
    builder.add_node("classify", classify_node)
    builder.add_node("stats", stats_node)
    builder.add_node("errors", errors_node)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("evaluator", evaluator_node)
    builder.add_node("report", report_node)

    # add_edge(START, "fetch"): every run of the graph begins at "fetch".
    builder.add_edge(START, "fetch")

    # fetch -> classify/stats/errors: three plain, UNCONDITIONAL edges
    # off one source node is exactly what makes LangGraph schedule and
    # run all three in parallel - no manual async/thread code needed.
    # This must stay unconditional (not routed by state["error"]) even
    # though all three no-op on a fetch error: aggregate_node below has
    # three static incoming edges and LangGraph's join waits for every
    # one of them to fire in the same step - if classify were
    # conditionally skipped on error, aggregate would be left waiting
    # on a branch that never ran. The "skip real work on error"
    # decision therefore lives inside each of the three nodes
    # themselves (see their docstrings), not in the routing.
    builder.add_edge("fetch", "classify")
    builder.add_edge("fetch", "stats")
    builder.add_edge("fetch", "errors")

    # All three feed into "aggregate", which LangGraph will not run
    # until every one of its incoming edges has fired (the "join").
    builder.add_edge("classify", "aggregate")
    builder.add_edge("stats", "aggregate")
    builder.add_edge("errors", "aggregate")

    builder.add_conditional_edges(
        "aggregate",
        route_after_aggregate,
        {"guardrail": "guardrail", "report": "report"},
    )

    builder.add_edge("guardrail", "analyze")
    builder.add_edge("analyze", "evaluator")
    builder.add_conditional_edges(
        "evaluator",
        route_after_evaluator,
        {"report": "report", "analyze": "analyze"},
    )

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

    `topic_allowed`/`security_allowed` are plain (overwritten-each-turn)
    booleans, one per input guardrail, mirroring ReportState's
    `analysis_ok` above - each guardrail node writes only its own key so
    the two can run concurrently with no write conflict (see
    build_chat_graph's fan-out below), and route_after_guardrail_gate
    reads both to decide whether the turn may reach agent_node.
    """

    messages: Annotated[list, add_messages]
    guardrail_flags: Annotated[list[str], operator.add]
    topic_allowed: bool
    security_allowed: bool


GuardrailFn = Callable[[str], dict[str, Any]]


def _has_tool_calls(message: Any) -> bool:
    return bool(getattr(message, "tool_calls", None))


def build_chat_graph(
    llm: Any = None,
    tools: Optional[list] = None,
    topic_guardrail_fn: GuardrailFn = classify_topic,
    security_guardrail_fn: GuardrailFn = classify_security_risk,
):
    """
    Construct and compile the interactive chat graph:

        START -+-> topic_guardrail    -+
               +-> security_guardrail -+-> guardrail_gate -> (allowed?) -> agent -> (tool_calls?) -> hitl/tool -> agent -> ... -> END
                                                            \\_(blocked)_> reject -> END          \\_____________(no tool_calls)____________/

    topic_guardrail and security_guardrail run concurrently (two plain,
    unconditional edges off START - the same fan-out mechanism used for
    fetch -> classify/stats/errors in the report graph above) and each
    independently classifies the user's newest message before agent_node
    ever runs: topic_guardrail rejects off-topic messages unrelated to
    tenders, security_guardrail rejects prompt-injection attempts or
    suspicious/dangerous database-extraction requests. guardrail_gate is
    a pure join/no-op (like aggregate_node above) that exists only to
    wait for both branches; route_after_guardrail_gate then requires
    BOTH to have allowed the turn before it reaches agent_node - either
    one blocking routes to reject_node instead, which appends a canned
    refusal AIMessage and ends the turn without ever invoking the LLM
    tool-calling loop.

    Classic ReAct loop after the gate: agent_node decides whether to
    call a tool or give a final answer; tool_node runs read-only tools
    immediately; hitl_node is scaffolding for a future write-tool
    (SIDE_EFFECT_TOOLS is empty today, so hitl_node is reachable but
    never actually pauses anything - see its docstring).

    `llm` is injectable for tests: pass a fake object exposing
    `.invoke(messages) -> AIMessage` (already "bound" to whatever
    canned tool-call sequence the test wants) to exercise the loop
    without ever calling a real model. Production code leaves it out
    and gets a real ChatOpenAI bound to `tools`.

    `tools` is injectable too, defaulting to the real six chat tools
    (agent.tools.TOOLS) - tests pass a couple of trivial fake tools
    instead, so exercising the loop never touches real MongoDB, on top
    of never calling a real model.

    `topic_guardrail_fn`/`security_guardrail_fn` are injectable the same
    way: pass a fake `lambda text: {"allowed": ..., "reason": ...}` to
    exercise the gate (or bypass it) without ever calling a real model.
    """
    resolved_tools = tools if tools is not None else TOOLS
    llm_with_tools = llm if llm is not None else get_chat_openai().bind_tools(resolved_tools)
    base_tool_node = ToolNode(resolved_tools)

    def agent_node(state: ChatState) -> dict[str, Any]:
        """
        Every tool takes absolute `start_date`/`end_date` strings (see
        agent.tools) - there is no "last N days" parameter the model can
        rely on. The model must therefore compute those dates itself
        from relative phrasing ("the last two days"), and it has no
        other way to know what "today" actually is - its training data
        has a fixed, stale cutoff. Without this reminder it silently
        guesses a training-cutoff-era date, computes a date range that
        never overlaps the real data, and the tools correctly (but
        misleadingly) come back empty.

        The reminder is injected fresh into the LLM call every turn,
        not added to `state["messages"]` - it must reflect "now" at
        call time, not the date the conversation started, and ChatState
        uses the add_messages reducer (messages only ever get appended),
        so persisting it here would duplicate it into history every turn.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        date_reminder = SystemMessage(
            content=(
                f"Today's date is {today}. Every tool takes start_date/end_date as "
                "absolute YYYY-MM-DD strings - when the user asks about a relative "
                "period (e.g. \"the last two days\", \"this week\"), compute the "
                "concrete dates yourself using today's date above."
            )
        )
        response = llm_with_tools.invoke([date_reminder, *state["messages"]])
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

    def _latest_user_text(state: ChatState) -> str:
        content = state["messages"][-1].content
        return content if isinstance(content, str) else str(content)

    def topic_guardrail_node(state: ChatState) -> dict[str, Any]:
        """
        Runs in parallel with security_guardrail_node; classifies only
        the just-appended user turn (state["messages"][-1]) - this node
        has a single incoming edge from START, so it always sees the
        turn fresh, before agent_node or anything else has run.
        """
        result = topic_guardrail_fn(_latest_user_text(state))
        update: dict[str, Any] = {"topic_allowed": bool(result.get("allowed"))}
        if not result.get("allowed"):
            update["guardrail_flags"] = [f"topic_guardrail_reject:{result.get('reason', '')}"]
        return update

    def security_guardrail_node(state: ChatState) -> dict[str, Any]:
        """Runs in parallel with topic_guardrail_node; see its docstring."""
        result = security_guardrail_fn(_latest_user_text(state))
        update: dict[str, Any] = {"security_allowed": bool(result.get("allowed"))}
        if not result.get("allowed"):
            update["guardrail_flags"] = [f"security_guardrail_reject:{result.get('reason', '')}"]
        return update

    def guardrail_gate_node(state: ChatState) -> dict[str, Any]:
        """
        The join point after the topic/security guardrail fan-out -
        exactly like aggregate_node above, a no-op that exists purely as
        a synchronization point: LangGraph won't run this node until
        both parallel guardrail branches have written their own,
        distinct ChatState keys (topic_allowed / security_allowed).
        """
        return {}

    def reject_node(state: ChatState) -> dict[str, Any]:
        """
        Reached only when guardrail_gate blocked the turn. Appends a
        canned refusal AIMessage - run_chat (agent/cli.py) always prints
        state["messages"][-1].content, so a blocked turn still needs a
        real AI-authored message here, not silence.
        """
        if not state.get("topic_allowed", True):
            refusal = "אני יכולה לעזור רק בשאלות על פעילות לוח המכרזים - הבקשה הזו לא נראית קשורה, אז אני לא יכולה לטפל בה."
        else:
            refusal = "הבקשה הזו סומנה ע\"י בדיקת אבטחה ולא ניתן לעבד אותה."
        return {"messages": [AIMessage(content=refusal)]}

    def route_after_guardrail_gate(state: ChatState) -> str:
        if state.get("topic_allowed", True) and state.get("security_allowed", True):
            return "agent"
        return "reject"

    builder = StateGraph(ChatState)
    builder.add_node("topic_guardrail", topic_guardrail_node)
    builder.add_node("security_guardrail", security_guardrail_node)
    builder.add_node("guardrail_gate", guardrail_gate_node)
    builder.add_node("reject", reject_node)
    builder.add_node("agent", agent_node)
    builder.add_node("hitl", hitl_node)
    builder.add_node("tool", tool_node)

    # START fans out to both guardrails via two plain, unconditional
    # edges - the same mechanism (see fetch -> classify/stats/errors
    # above) that makes LangGraph schedule and run them concurrently.
    builder.add_edge(START, "topic_guardrail")
    builder.add_edge(START, "security_guardrail")
    builder.add_edge("topic_guardrail", "guardrail_gate")
    builder.add_edge("security_guardrail", "guardrail_gate")
    builder.add_conditional_edges(
        "guardrail_gate",
        route_after_guardrail_gate,
        {"agent": "agent", "reject": "reject"},
    )
    builder.add_edge("reject", END)

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
