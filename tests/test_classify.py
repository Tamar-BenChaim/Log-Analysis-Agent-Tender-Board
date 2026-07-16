"""
Unit tests for agent.nodes.classify (originally Story SCRUM-38).

Test messages are copied verbatim (or near-verbatim) from real
test.applicationlogs documents, to make sure the classifier actually
matches production log shapes and not an idealized guess.
"""

from agent.nodes.classify import (
    CREATE,
    DELETE,
    EDIT,
    INVALID,
    OTHER,
    REGISTER,
    VIEW,
    _is_error_record,
    classify_log_record,
    count_tender_events,
)


def _msg(text: str) -> dict:
    """Shorthand for building a minimal log record with just a message."""
    return {"message": text}


# --- classify_log_record: HTTP access-log lines -----------------------


def test_http_post_create_endpoint_is_create():
    assert classify_log_record(_msg("POST /tender-board 201 241ms")) == CREATE


def test_http_post_smart_create_endpoint_is_create():
    assert classify_log_record(_msg("POST /tender-board/smart-create 201 1743ms")) == CREATE


def test_http_post_apply_endpoint_is_register():
    assert (
        classify_log_record(_msg("POST /tender-board/6a4b8b1e851d47b859934fd4/apply 200 1869ms"))
        == REGISTER
    )


def test_http_put_endpoint_is_edit():
    assert classify_log_record(_msg("PUT /tender-board/6a4fb16677f27b411f44e6cf 200 352ms")) == EDIT


def test_http_delete_endpoint_is_delete():
    assert classify_log_record(_msg("DELETE /tender-board/6a4fb16677f27b411f44e6cf 200 12ms")) == DELETE


def test_http_get_endpoint_is_view():
    assert classify_log_record(_msg("GET /tender-board/product-types 200 4ms")) == VIEW


def test_http_get_smart_search_is_view():
    assert classify_log_record(_msg("GET /tender-board/smart-search?q=foo 200 1044ms")) == VIEW


# --- classify_log_record: logger.info(...) lines ----------------------


def test_logger_tender_created_is_create():
    assert classify_log_record(_msg("Tender created successfully")) == CREATE


def test_logger_tender_updated_is_edit():
    assert classify_log_record(_msg("Tender updated successfully")) == EDIT


def test_logger_tender_deleted_is_delete():
    assert classify_log_record(_msg("Tender deleted successfully")) == DELETE


def test_logger_tender_closed_is_delete_per_story_decision():
    # Confirmed decision: closeTender's soft-delete counts as `delete`.
    assert classify_log_record(_msg("Tender closed successfully")) == DELETE


def test_logger_applicant_registered_is_register():
    assert classify_log_record(_msg("Applicant registered successfully to tender")) == REGISTER


def test_logger_failed_apply_still_counts_as_register():
    # Confirmed decision: failures count under their normal category.
    assert classify_log_record(_msg("Apply to tender failed")) == REGISTER


def test_logger_fetched_tenders_list_is_view():
    assert classify_log_record(_msg("Fetched tenders list")) == VIEW


def test_logger_fetched_tender_details_is_view():
    assert classify_log_record(_msg("Fetched tender details")) == VIEW


# --- classify_log_record: system/internal noise -> other ---------------


def test_system_message_starting_ai_generation_is_other():
    assert classify_log_record(_msg("Starting AI tender data generation")) == OTHER


def test_system_message_backfilling_embeddings_is_other():
    assert classify_log_record(_msg("Backfilling embeddings for 8 tenders")) == OTHER


def test_unrelated_message_is_other():
    assert classify_log_record(_msg("MongoDB connected successfully")) == OTHER


# --- classify_log_record: corrupted / missing records -> invalid -------


def test_missing_message_field_is_invalid():
    assert classify_log_record({"timestamp": "2026-01-01"}) == INVALID


def test_none_message_is_invalid():
    assert classify_log_record(_msg(None)) == INVALID  # type: ignore[arg-type]


def test_non_string_message_is_invalid():
    assert classify_log_record({"message": 12345}) == INVALID


def test_empty_string_message_is_invalid():
    assert classify_log_record(_msg("   ")) == INVALID


def test_non_dict_record_is_invalid():
    assert classify_log_record("not-a-dict") == INVALID  # type: ignore[arg-type]


# --- count_tender_events: aggregation -----------------------------------


def test_count_tender_events_tallies_every_category():
    records = [
        _msg("Tender created successfully"),
        _msg("Tender created successfully"),
        _msg("Applicant registered successfully to tender"),
        _msg("Tender updated successfully"),
        _msg("Tender closed successfully"),
        _msg("Fetched tenders list"),
        _msg("Starting AI tender data generation"),
        {"timestamp": "no message field here"},
    ]

    counts = count_tender_events(records)

    assert counts == {
        "create": 2,
        "register": 1,
        "edit": 1,
        "delete": 1,
        "view": 1,
        "other": 1,
        "invalid": 1,
    }


def test_count_tender_events_on_empty_list_returns_all_zeros():
    counts = count_tender_events([])

    assert counts == {
        "create": 0,
        "register": 0,
        "edit": 0,
        "delete": 0,
        "view": 0,
        "other": 0,
        "invalid": 0,
    }


# --- Enriched log schema (SCRUM-161): module-tagged records -------------


def test_module_tagged_record_without_tender_word_still_classifies_by_keyword():
    # No literal "tender" substring anywhere, but a real keyword rule
    # ("applicant registered") still matches regardless of `module`.
    record = {
        "message": "Applicant registered successfully",
        "module": "tenderBoard",
        "requestId": "req-1",
    }
    assert classify_log_record(record) == REGISTER


def test_module_tagged_record_with_unrecognized_wording_falls_to_other():
    # A real business event whose exact phrasing isn't in the keyword
    # table yet - OTHER is the honest outcome, not a special category.
    record = {"message": "Applicant approved", "module": "tenderBoard"}
    assert classify_log_record(record) == OTHER


def test_old_format_record_without_module_key_classifies_exactly_as_before():
    # Backward compatibility: absence of `module` must not change the
    # outcome for a message that already matches a keyword rule.
    with_module = classify_log_record({"message": "Tender updated successfully", "module": "tenderBoard"})
    without_module = classify_log_record({"message": "Tender updated successfully"})
    assert with_module == without_module == EDIT


# --- _is_error_record (used by agent.nodes.errors in SCRUM-166) --------


def test_is_error_record_true_when_level_is_error():
    assert _is_error_record({"level": "error", "message": "Tender created successfully"}) is True


def test_is_error_record_true_when_stack_present():
    assert _is_error_record({"message": "Apply to tender failed", "stack": "Error: boom\n at ..."}) is True


def test_is_error_record_false_for_normal_info_record():
    assert _is_error_record({"level": "info", "message": "Tender created successfully"}) is False


def test_is_error_record_false_when_no_level_or_stack_present():
    assert _is_error_record({"message": "Tender created successfully"}) is False
