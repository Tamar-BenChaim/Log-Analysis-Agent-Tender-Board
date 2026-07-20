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
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"allowed", "reason"}


def _is_valid_result(parsed: Any) -> bool:
    return (
        isinstance(parsed, dict)
        and REQUIRED_KEYS.issubset(parsed.keys())
        and isinstance(parsed.get("allowed"), bool)
    )


def run_json_classifier(
    llm: Any, prompt: str, *, fail_open_on_error: bool, node: str = "llm_classifier"
) -> dict[str, Any]:
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

    Each invoke() attempt logs a linked start/end JSON pair (same shape
    as the future agentsLogger.ts port this project will eventually
    plug into - see docs/agentsLogger.md) via the stdlib `logging`
    module, keyed by a fresh `invocation_id` per attempt.
    """
    response = None
    last_exc: Optional[Exception] = None
    for _attempt in range(2):
        invocation_id = str(uuid.uuid4())
        # TODO: share one run_id per turn once the future logger module exists
        run_id = str(uuid.uuid4())
        logger.info(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "parent_run_id": None,
            "agent_name": "tender-board-chat-agent",
            "node": node,
            "node_type": "llm",
            "invocation_id": invocation_id,
            "phase": "start",
            "status": None,
            "description": f"{node} JSON classification call (attempt {_attempt + 1})",
            "input": {"system_prompt": None, "user_prompt": prompt},
            "tags": ["guardrail", node],
            "metadata": {"attempt": _attempt + 1, "fail_open_on_error": fail_open_on_error},
        }, default=str))

        start = time.monotonic()
        try:
            response = llm.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - any transport/client error is retryable once
            last_exc = exc
            logger.error(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "invocation_id": invocation_id,
                "phase": "end",
                "status": "error",
                "duration_ms": (time.monotonic() - start) * 1000,
                "output": None,
                "error": str(exc),
            }, default=str))
            continue

        last_exc = None
        logger.info(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "invocation_id": invocation_id,
            "phase": "end",
            "status": "success",
            "duration_ms": (time.monotonic() - start) * 1000,
            "output": response.content if hasattr(response, "content") else str(response),
            "error": None,
        }, default=str))
        break

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
