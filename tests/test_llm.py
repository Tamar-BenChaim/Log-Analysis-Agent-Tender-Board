"""
Unit tests for agent.llm (shared ChatOpenAI construction).

No real network calls anywhere - constructing a ChatOpenAI instance
does not itself contact the API, so these only check the env-var
parsing logic and that a real ChatOpenAI object comes back either way.
"""

import pytest
from langchain_openai import ChatOpenAI

from agent.llm import _ssl_verification_disabled, get_chat_openai


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("OPENAI_DISABLE_SSL_VERIFY", raising=False)


def test_ssl_verification_disabled_is_false_by_default():
    assert _ssl_verification_disabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "Yes"])
def test_ssl_verification_disabled_true_for_truthy_values(monkeypatch, value):
    monkeypatch.setenv("OPENAI_DISABLE_SSL_VERIFY", value)
    assert _ssl_verification_disabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_ssl_verification_disabled_false_for_falsy_values(monkeypatch, value):
    monkeypatch.setenv("OPENAI_DISABLE_SSL_VERIFY", value)
    assert _ssl_verification_disabled() is False


def test_get_chat_openai_returns_a_chat_openai_instance_by_default():
    assert isinstance(get_chat_openai(), ChatOpenAI)


def test_get_chat_openai_returns_a_chat_openai_instance_with_ssl_verify_disabled(monkeypatch):
    monkeypatch.setenv("OPENAI_DISABLE_SSL_VERIFY", "true")
    assert isinstance(get_chat_openai(), ChatOpenAI)


def test_get_chat_openai_respects_model_argument():
    llm = get_chat_openai("gpt-4o-mini")
    assert llm.model_name == "gpt-4o-mini"
