"""
Shared LLM-call/parse/fallback plumbing for the yes/no chat-input
guardrails (topic_guardrail, security_guardrail).

Both guardrails share the exact same response contract -
{"allowed": bool, "reason": str} - and the exact same "call the model,
parse JSON, never raise" shape already used by agent.nodes.analyze's
single LLM call (agent/nodes/analyze.py). Factored out here so neither
guardrail file duplicates that plumbing; each guardrail still owns its
own prompt and its own LLM instance - this module has no opinion on
what is being classified.
"""

from __future__ import annotations

import json
from typing import Any, Optional

REQUIRED_KEYS = {"allowed", "reason"}


def _is_valid_result(parsed: Any) -> bool:
    return (
        isinstance(parsed, dict)
        and REQUIRED_KEYS.issubset(parsed.keys())
        and isinstance(parsed.get("allowed"), bool)
    )


def run_json_classifier(llm: Any, prompt: str, *, fail_open_on_error: bool) -> dict[str, Any]:
    """
    Call llm.invoke(prompt) and parse a {"allowed": bool, "reason": str}
    response. Never raises.

    Two distinct failure modes, two distinct policies:
      - The call itself fails (network/timeout): retried once
        immediately - a single transient blip shouldn't block a whole
        chat turn - then falls back to `fail_open_on_error` if it still
        fails. A sustained outage therefore still blocks every user by
        default (fail_open_on_error=False) - the deliberate,
        safety-first default for both guardrails using this helper.
      - The call succeeds but the response can't be parsed as the
        expected shape: ALWAYS fail-closed (allowed=False), regardless
        of fail_open_on_error - a malformed response is never treated
        as an implicit pass.
    """
    response = None
    last_exc: Optional[Exception] = None
    for _attempt in range(2):
        try:
            response = llm.invoke(prompt)
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 - any transport/client error is retryable once
            last_exc = exc

    if last_exc is not None:
        return {
            "allowed": fail_open_on_error,
            "reason": f"guardrail_error:llm_call_failed:{last_exc.__class__.__name__}",
        }

    content = response.content if hasattr(response, "content") else str(response)

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"allowed": False, "reason": "guardrail_error:unparseable_response"}

    if not _is_valid_result(parsed):
        return {"allowed": False, "reason": "guardrail_error:malformed_response"}

    return {"allowed": parsed["allowed"], "reason": str(parsed.get("reason", ""))}
