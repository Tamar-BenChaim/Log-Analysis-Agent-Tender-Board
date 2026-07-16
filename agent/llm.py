"""
Shared ChatOpenAI construction, used by agent.graph's chat graph
(agent_node), agent.nodes.analyze's report-graph analyze_node, and
evals/eval.py's --live mode - one place to build the client so all
three stay consistent.

Some local networks (school/corporate proxies doing TLS interception)
break certificate verification for arbitrary outbound HTTPS calls, not
just this agent's - the Node/Express backend this project reads logs
from already documents the identical issue via its own
NODE_TLS_REJECT_UNAUTHORIZED=0 in .env. OPENAI_DISABLE_SSL_VERIFY is
the equivalent opt-in escape hatch for this agent's own calls to the
OpenAI API: OFF by default (real certificate verification - the safe
choice for anyone not on such a network) and only disabled when
explicitly set, never a silent, unconditional bypass baked into the
shipped agent.
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

DEFAULT_MODEL = "gpt-4o-mini"

_TRUTHY_VALUES = {"1", "true", "yes"}


def _ssl_verification_disabled() -> bool:
    return os.environ.get("OPENAI_DISABLE_SSL_VERIFY", "").strip().lower() in _TRUTHY_VALUES


def get_chat_openai(model: str = DEFAULT_MODEL) -> ChatOpenAI:
    """
    Build a ChatOpenAI instance for `model`, honoring
    OPENAI_DISABLE_SSL_VERIFY when set. The common case (verification
    stays on) never touches httpx directly - only the opt-in path
    constructs custom clients with verify=False.
    """
    if not _ssl_verification_disabled():
        return ChatOpenAI(model=model)

    import httpx  # local import: only needed on this opt-in path

    return ChatOpenAI(
        model=model,
        http_client=httpx.Client(verify=False),
        http_async_client=httpx.AsyncClient(verify=False),
    )
