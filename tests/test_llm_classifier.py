"""Unit tests for agent.nodes.llm_classifier.run_json_classifier. No real LLM calls."""

import json
import logging

from agent.nodes.llm_classifier import run_json_classifier


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content
        self.call_count = 0

    def invoke(self, prompt):
        self.call_count += 1
        return _FakeLLMResponse(self._content)


class _FailNTimesThenSucceedLLM:
    """Raises on the first `fail_times` calls, then returns a real response."""

    def __init__(self, fail_times, content):
        self._fail_times = fail_times
        self._content = content
        self.call_count = 0

    def invoke(self, prompt):
        self.call_count += 1
        if self.call_count <= self._fail_times:
            raise ConnectionError("simulated network failure")
        return _FakeLLMResponse(self._content)


class _AlwaysFailsLLM:
    def __init__(self):
        self.call_count = 0

    def invoke(self, prompt):
        self.call_count += 1
        raise TimeoutError("simulated timeout")


def test_run_json_classifier_returns_allowed_true_on_valid_response():
    fake_llm = _FakeLLM(json.dumps({"allowed": True, "reason": ""}))

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    assert result == {"allowed": True, "reason": ""}


def test_run_json_classifier_returns_allowed_false_on_valid_response():
    fake_llm = _FakeLLM(json.dumps({"allowed": False, "reason": "off_topic"}))

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    assert result == {"allowed": False, "reason": "off_topic"}


def test_run_json_classifier_fails_closed_on_invalid_json_regardless_of_flag():
    fake_llm = _FakeLLM("this is not JSON")

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=True)

    assert result["allowed"] is False
    assert result["reason"] == "guardrail_error:unparseable_response"


def test_run_json_classifier_fails_closed_on_missing_fields_regardless_of_flag():
    fake_llm = _FakeLLM(json.dumps({"allowed": True}))  # missing "reason"

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=True)

    assert result["allowed"] is False
    assert result["reason"] == "guardrail_error:malformed_response"


def test_run_json_classifier_fails_closed_on_non_boolean_allowed():
    fake_llm = _FakeLLM(json.dumps({"allowed": "yes", "reason": "x"}))

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=True)

    assert result["allowed"] is False
    assert result["reason"] == "guardrail_error:malformed_response"


def test_run_json_classifier_retries_once_and_recovers_from_a_single_transient_failure():
    fake_llm = _FailNTimesThenSucceedLLM(fail_times=1, content=json.dumps({"allowed": True, "reason": ""}))

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    assert result == {"allowed": True, "reason": ""}
    assert fake_llm.call_count == 2


def test_run_json_classifier_fails_open_on_sustained_network_error_when_configured():
    fake_llm = _AlwaysFailsLLM()

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=True)

    assert result["allowed"] is True
    assert result["reason"].startswith("guardrail_error:llm_call_failed:")
    assert fake_llm.call_count == 2  # one retry, then gives up


def test_run_json_classifier_fails_closed_on_sustained_network_error_by_default():
    fake_llm = _AlwaysFailsLLM()

    result = run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    assert result["allowed"] is False
    assert result["reason"].startswith("guardrail_error:llm_call_failed:")


def _parsed_log_records(caplog):
    return [json.loads(record.message) for record in caplog.records]


def test_run_json_classifier_logs_linked_start_end_pair_with_given_node(caplog):
    caplog.set_level(logging.INFO, logger="agent.nodes.llm_classifier")
    fake_llm = _FakeLLM(json.dumps({"allowed": True, "reason": ""}))

    run_json_classifier(fake_llm, "prompt", fail_open_on_error=False, node="topic_guardrail")

    entries = _parsed_log_records(caplog)
    assert len(entries) == 2
    start, end = entries
    assert start["phase"] == "start"
    assert start["status"] is None
    assert start["node"] == "topic_guardrail"
    assert start["node_type"] == "llm"
    assert start["input"]["user_prompt"] == "prompt"
    assert end["phase"] == "end"
    assert end["status"] == "success"
    assert end["invocation_id"] == start["invocation_id"]
    assert end["duration_ms"] >= 0
    assert end["error"] is None


def test_run_json_classifier_defaults_node_when_not_given(caplog):
    caplog.set_level(logging.INFO, logger="agent.nodes.llm_classifier")
    fake_llm = _FakeLLM(json.dumps({"allowed": True, "reason": ""}))

    run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    start = _parsed_log_records(caplog)[0]
    assert start["node"] == "llm_classifier"


def test_run_json_classifier_logs_error_status_on_each_failed_attempt(caplog):
    caplog.set_level(logging.INFO, logger="agent.nodes.llm_classifier")
    fake_llm = _AlwaysFailsLLM()

    run_json_classifier(fake_llm, "prompt", fail_open_on_error=False)

    entries = _parsed_log_records(caplog)
    # Two attempts, each a start/end pair -> 4 entries, both ends are errors.
    assert len(entries) == 4
    ends = [e for e in entries if e["phase"] == "end"]
    assert len(ends) == 2
    assert all(e["status"] == "error" for e in ends)
    assert all(e["error"] for e in ends)
