"""Unit tests for agent.nodes.topic_guardrail. No real LLM calls."""

import json

from agent.nodes.topic_guardrail import classify_topic


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content
        self.seen_prompt = None

    def invoke(self, prompt):
        self.seen_prompt = prompt
        return _FakeLLMResponse(self._content)


def test_classify_topic_allows_a_tender_related_question():
    fake_llm = _FakeLLM(json.dumps({"allowed": True, "reason": ""}))

    result = classify_topic("how many tenders were created this week?", llm=fake_llm)

    assert result == {"allowed": True, "reason": ""}
    assert "how many tenders were created this week?" in fake_llm.seen_prompt


def test_classify_topic_blocks_an_unrelated_question():
    fake_llm = _FakeLLM(json.dumps({"allowed": False, "reason": "off_topic"}))

    result = classify_topic("what's a good recipe for pasta?", llm=fake_llm)

    assert result == {"allowed": False, "reason": "off_topic"}


def test_classify_topic_falls_back_to_fail_closed_on_unparseable_response():
    fake_llm = _FakeLLM("not json")

    result = classify_topic("some question", llm=fake_llm)

    assert result["allowed"] is False
