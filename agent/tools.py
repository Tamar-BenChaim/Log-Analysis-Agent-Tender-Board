"""
Chat-mode tool list (originally scaffolded in Story SCRUM-170, populated
in SCRUM-174).

Empty today on purpose - the `report` subcommand (agent/graph.py's
build_graph) does not use tools at all. This module exists now so
agent/cli.py's `chat` subcommand has a stable import target, and so
SCRUM-174 has an obvious, single place to add the six planned tools
(get_error_count, get_request_trace, get_user_activity,
find_duplicate_tenders, get_latency_stats, get_tender_creation_volume)
without touching agent/graph.py's report-graph wiring.
"""

from __future__ import annotations

TOOLS: list = []
