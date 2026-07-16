"""
Validates analyze_node's output against the underlying numeric state
(Story SCRUM-180).

evaluate_analysis() does NOT judge "is this good prose" - it judges "is
this internally consistent with the data analyze_node was actually
given". A response that hallucinates an anomaly not backed by the
numbers, or that isn't even valid JSON in the first place, fails and
triggers a retry (capped at MAX_ANALYSIS_ATTEMPTS by the graph).
"""

from __future__ import annotations

from typing import Any

REQUIRED_KEYS = {"business_logic_notes", "error_patterns", "anomalies", "confidence"}
CONFIDENCE_THRESHOLD = 0.5
MAX_ANALYSIS_ATTEMPTS = 3


def evaluate_analysis(
    analysis: dict[str, Any],
    errors: dict[str, Any],
    anomalies: dict[str, Any],
) -> bool:
    """
    Returns True (pass, safe to show in the report) or False (retry-worthy fail).

    Checks, in order - any failure short-circuits the rest:
      1. Structural validity: all required keys present, `confidence`
         is actually numeric. A malformed/unparseable response
         (agent.nodes.analyze._empty_analysis) fails here immediately.
      2. No hallucinated anomaly: the model's own `anomalies` narration
         mentioning "duplicate" when no duplicates were actually
         detected, or mentioning error-spike/error-pattern language
         when the error total is zero, means it invented something not
         backed by the data it was given.
      3. `confidence` below CONFIDENCE_THRESHOLD is a soft-fail worth
         one retry - the model itself wasn't sure.
    """
    if not REQUIRED_KEYS.issubset(analysis.keys()):
        return False
    if not isinstance(analysis.get("confidence"), (int, float)) or isinstance(analysis.get("confidence"), bool):
        return False
    if not isinstance(analysis.get("anomalies"), list) or not isinstance(analysis.get("error_patterns"), list):
        return False

    anomaly_text = " ".join(str(item) for item in analysis["anomalies"]).lower()
    if "duplicate" in anomaly_text and not anomalies.get("duplicates"):
        return False
    if ("error spike" in anomaly_text or "error pattern" in anomaly_text) and errors.get("total", 0) == 0:
        return False

    if analysis["confidence"] < CONFIDENCE_THRESHOLD:
        return False

    return True
