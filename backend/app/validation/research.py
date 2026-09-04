from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.graph.state import ResearchResult


MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

VERIFICATION_FIELDS = {
    "gst_status",
    "mca_status",
    "website_status",
}

# verification_status values that mean "the research layer already decided this
# result does not describe the target entity / is not usable as factual evidence".
REJECTED_VERIFICATION_STATUSES = {
    "REJECTED",
    "ENTITY_MISMATCH",
    "UNRELATED",
    "IRRELEVANT",
}

ADDRESS_FIELDS = {
    "registered_address", "establishment_address", "contact_address",
    "principal_business_address", "principal_place_of_business", "corporate_address", "address",
}

LEGAL_NAME_FIELDS = {
    "legal_name", "company_name", "business_name", "establishment_name", "trade_name",
}

BUSINESS_ACTIVITY_FIELDS = {
    "business_activity", "nature_of_business", "activity", "principal_business_activity",
}


class ResearchResultValidation(BaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    result: Optional[ResearchResult] = None


class ResearchResultsValidation(BaseModel):
    valid_results: list[ResearchResult] = Field(default_factory=list)
    invalid_results: list[ResearchResult] = Field(default_factory=list)
    validations: list[ResearchResultValidation] = Field(
        default_factory=list
    )


def _is_valid_url(value: str) -> bool:
    parsed = urlparse(value)

    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
    )


def validate_research_result(
    result: ResearchResult,
) -> ResearchResultValidation:
    errors: list[str] = []

    if result.field_value is None:
        errors.append("field_value is missing")
    elif isinstance(result.field_value, str):
        if not result.field_value.strip():
            errors.append("field_value is empty")

    if not MIN_CONFIDENCE <= result.confidence <= MAX_CONFIDENCE:
        errors.append(
            "confidence must be between 0.0 and 1.0"
        )
    elif result.confidence <= 0.0:
        errors.append("confidence must be greater than 0.0 to be admitted as factual evidence")

    if not result.result_id.strip():
        errors.append("result_id is empty")

    if not result.task_id.strip():
        errors.append("task_id is empty")

    if not result.field_name.strip():
        errors.append("field_name is empty")

    if not result.source_name.strip():
        errors.append("source_name is empty")

    if result.source_url is not None:
        if not _is_valid_url(result.source_url):
            errors.append("source_url is invalid")

    PLACEHOLDER_STATUS_VALUES = {
        "NOT_FOUND",
        "UNAVAILABLE",
        "UNKNOWN",
        "ERROR",
        "BLOCKED",
        "IRRELEVANT",
        "SOURCE_UNAVAILABLE",
        "CAPTCHA_REQUIRED",
        "NOT APPLICABLE",
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "SOMETHING WENT WRONG",
    }
    if isinstance(result.field_value, str) and result.field_value.strip().upper() in PLACEHOLDER_STATUS_VALUES:
        if (
            result.field_name in VERIFICATION_FIELDS
            and result.field_value.upper() == "UNAVAILABLE"
        ):
            errors.append("verification result is unavailable")
        else:
            errors.append(f"field_value '{result.field_value}' is a placeholder/status value, not factual evidence")

    # Anything the research layer already marked as not entity-relevant (wrong
    # company page, search/navigation page) must never be persisted as evidence.
    if str(getattr(result, "verification_status", "") or "").strip().upper() in REJECTED_VERIFICATION_STATUSES:
        errors.append(
            f"verification_status '{result.verification_status}' — result rejected upstream as not entity-relevant"
        )

    from app.research.base import (
        is_address_like,
        is_valid_business_activity,
        is_valid_legal_name,
    )

    # Semantic validation for address fields
    if result.field_name in ADDRESS_FIELDS:
        if isinstance(result.field_value, str) and not is_address_like(result.field_value):
            errors.append(f"field_value '{result.field_value}' is not a valid address structure")

    # Semantic validation for company legal name fields
    if result.field_name in LEGAL_NAME_FIELDS:
        if isinstance(result.field_value, str) and not is_valid_legal_name(result.field_value):
            errors.append(f"field_value '{result.field_value}' is not a valid legal company name")

    # Semantic validation for business-activity fields: unrelated / search /
    # navigation text, page dumps, bare labels and identifiers cannot become
    # business-activity evidence.
    if result.field_name in BUSINESS_ACTIVITY_FIELDS:
        if isinstance(result.field_value, str) and not is_valid_business_activity(result.field_value):
            errors.append(
                f"field_value '{result.field_value[:80]}' is not a valid business-activity description"
            )

    return ResearchResultValidation(
        is_valid=not errors,
        errors=errors,
        result=result,
    )


def validate_research_results(
    results: list[ResearchResult],
) -> ResearchResultsValidation:
    validations = [
        validate_research_result(result)
        for result in results
    ]

    return ResearchResultsValidation(
        valid_results=[
            validation.result
            for validation in validations
            if validation.is_valid
            and validation.result is not None
        ],
        invalid_results=[
            validation.result
            for validation in validations
            if not validation.is_valid
            and validation.result is not None
        ],
        validations=validations,
    )
