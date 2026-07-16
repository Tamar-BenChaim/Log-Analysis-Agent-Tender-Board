"""Unit tests for agent.nodes.analyze (Story SCRUM-180). No real LLM calls."""

import json

from agent.nodes.analyze import analyze_records


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, prompt):
        return _FakeLLMResponse(self._content)


def test_analyze_records_returns_parsed_json_on_valid_response():
    valid_json = json.dumps(
        {"business_logic_notes": "All good.", "error_patterns": [], "anomalies": [], "confidence": 0.95}
    )
    fake_llm = _FakeLLM(valid_json)

    result = analyze_records({"create": 1}, {"total": 0}, {"duplicates": []}, ["sample text"], llm=fake_llm)

    assert result["business_logic_notes"] == "All good."
    assert result["confidence"] == 0.95


def test_analyze_records_falls_back_to_empty_analysis_on_invalid_json():
    fake_llm = _FakeLLM("this is not valid JSON at all")

    result = analyze_records({"create": 1}, {"total": 0}, {"duplicates": []}, [], llm=fake_llm)

    assert result["business_logic_notes"] == ""
    assert result["error_patterns"] == []
    assert result["anomalies"] == []
    assert result["confidence"] == 0.0
    assert "_raw" in result


def test_analyze_records_falls_back_when_json_is_not_an_object():
    fake_llm = _FakeLLM(json.dumps([1, 2, 3]))

    result = analyze_records({"create": 1}, {"total": 0}, {"duplicates": []}, [], llm=fake_llm)

    assert result["confidence"] == 0.0


class _ObjectIdLike:
    """Stand-in for pymongo.ObjectId: not JSON-serializable, but str()-able."""

    def __str__(self):
        return "6a57feae2d475280fb704c2c"


def test_analyze_records_prompt_handles_non_json_serializable_ids():
    # Real duplicate-tender anomalies carry Mongo ObjectId values (not
    # plain strings) in tender_ids/request_ids - json.dumps must not
    # crash building the prompt just because a real anomaly was found.
    fake_llm = _FakeLLM(
        json.dumps({"business_logic_notes": "ok", "error_patterns": [], "anomalies": [], "confidence": 0.9})
    )
    anomalies = {
        "duplicates": [{"tender_ids": [_ObjectIdLike(), _ObjectIdLike()], "seconds_apart": 0.6}],
        "slow_requests": [],
    }

    result = analyze_records({"create": 1}, {"total": 0}, anomalies, [], llm=fake_llm)

    assert result["confidence"] == 0.9
