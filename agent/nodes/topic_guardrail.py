"""
Topic-scope guardrail on the user's own chat input.

Runs before agent_node (agent/graph.py) and blocks any chat turn whose
text isn't a request for information about tenders (the Tender Board
domain this agent exists to answer questions about) - small talk and
unrelated requests are rejected before the ReAct loop ever starts.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.llm import get_chat_openai
from agent.nodes.llm_classifier import run_json_classifier

MAX_INPUT_LENGTH = 4000

# Network/timeout failures here fail CLOSED by default: this guardrail is
# a mandatory gate, not a best-effort filter - see agent/nodes/llm_classifier.py.
FAIL_OPEN_ON_ERROR = False

_SCHEMA_INSTRUCTIONS = (
    'Respond with ONLY a JSON object of this exact shape, no other text:\n'
    '{"allowed": <true|false>, "reason": "<short phrase>"}'
)

def _build_prompt(text: str) -> str:
    # Built by concatenation, not str.format(), because _SCHEMA_INSTRUCTIONS
    # itself contains literal `{...}` JSON braces that .format() would try
    # (and fail) to interpret as replacement fields.
    return (
        "You are a topic-scope filter for a chat assistant that answers questions "
        "about Tender Board activity (tenders, requests, errors, statistics).\n\n"
        f'User message: "{text}"\n\n'
        "Decide whether this message is a request for information related to "
        "tenders / the Tender Board (allowed=true), or an unrelated/off-topic "
        "message such as small talk or a general-knowledge question that has "
        "nothing to do with tenders (allowed=false).\n\n" + _SCHEMA_INSTRUCTIONS
    )


def classify_topic(text: str, llm: Optional[Any] = None) -> dict[str, Any]:
    """
    Classify whether `text` is on-topic for the Tender Board chat.

    `llm` is injectable for tests (same DI pattern as
    agent.nodes.analyze.analyze_records); production code leaves it out
    and gets a real ChatOpenAI.
    """
    resolved_llm = llm if llm is not None else get_chat_openai()
    prompt = _build_prompt(text[:MAX_INPUT_LENGTH])
    return run_json_classifier(resolved_llm, prompt, fail_open_on_error=FAIL_OPEN_ON_ERROR)
