from app.graph.state import ResearchResult
from app.validation.research import (
    validate_research_result,
    validate_research_results,
)


def make_result(**overrides):
    data = {
        "result_id": "RESULT-TASK-001-001",
        "task_id": "TASK-001",
        "field_name": "legal_name",
        "field_value": "ABC Foods Private Limited",
        "source_name": "GST Portal",
        "source_url": "https://www.gst.gov.in",
        "retrieved_at": "2026-08-26T10:00:00+00:00",
        "confidence": 0.95,
    }

    data.update(overrides)

    return ResearchResult(**data)


def test_valid_result_is_accepted():
    result = make_result()

    validation = validate_research_result(result)

    assert validation.is_valid is True
    assert validation.result == result
    assert validation.errors == []


def test_empty_field_value_is_rejected():
    result = make_result(field_value="")

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "field_value is empty" in validation.errors


def test_none_field_value_is_rejected():
    result = make_result(field_value=None)

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "field_value is missing" in validation.errors


def test_invalid_confidence_is_rejected():
    result = make_result(confidence=1.5)

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "confidence must be between 0.0 and 1.0" in validation.errors


def test_missing_source_name_is_rejected():
    result = make_result(source_name="")

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "source_name is empty" in validation.errors


def test_invalid_source_url_is_rejected():
    result = make_result(source_url="not-a-url")

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "source_url is invalid" in validation.errors


def test_unavailable_status_is_rejected_for_verification_result():
    result = make_result(
        field_name="gst_status",
        field_value="UNAVAILABLE",
    )

    validation = validate_research_result(result)

    assert validation.is_valid is False
    assert "verification result is unavailable" in validation.errors


def test_valid_results_are_separated_from_invalid_results():
    valid_result = make_result()

    invalid_result = make_result(
        result_id="RESULT-TASK-001-002",
        field_value="",
    )

    validation = validate_research_results(
        [valid_result, invalid_result]
    )

    assert validation.valid_results == [valid_result]
    assert validation.invalid_results == [invalid_result]
