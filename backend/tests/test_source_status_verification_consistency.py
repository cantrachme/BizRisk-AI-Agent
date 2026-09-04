"""
Regression: report source-level VERIFIED status must reflect the evidence's own
verification_status == "VERIFIED", not raw confidence alone.

Legacy fallback to `confidence >= 0.70` applies only when verification_status is
genuinely absent (None / ""), never for an explicit "UNVERIFIED".
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.services.report import (
    _classify_source_status,
    _evidence_is_verified,
    build_verification_summary,
)


def _ev(field_name="company_status", field_value="ACTIVE", source_name="General Web",
        confidence=0.60, verification_status="UNVERIFIED"):
    return SimpleNamespace(
        field_name=field_name,
        field_value=field_value,
        source_name=source_name,
        confidence=confidence,
        verification_status=verification_status,
    )


# --- _evidence_is_verified --------------------------------------------------- #
def test_high_confidence_but_unverified_is_not_verified():
    assert _evidence_is_verified(_ev(confidence=0.95, verification_status="UNVERIFIED")) is False


def test_explicit_verified_status_counts():
    assert _evidence_is_verified(_ev(confidence=0.72, verification_status="VERIFIED")) is True


def test_legacy_absent_status_falls_back_to_confidence():
    assert _evidence_is_verified(_ev(confidence=0.80, verification_status=None)) is True
    assert _evidence_is_verified(_ev(confidence=0.60, verification_status=None)) is False
    assert _evidence_is_verified(_ev(confidence=0.80, verification_status="")) is True


def test_placeholder_value_never_verified():
    assert _evidence_is_verified(
        _ev(field_value="NOT_FOUND", confidence=0.95, verification_status="VERIFIED")
    ) is False


# --- _classify_source_status ---------------------------------------------------#
def test_source_with_only_unverified_high_confidence_is_not_verified():
    status, _ = _classify_source_status([
        _ev(field_name="legal_name", field_value="ACME WIDGETS PRIVATE LIMITED",
            confidence=0.75, verification_status="UNVERIFIED"),
        _ev(field_name="business_activity", field_value="Software development services",
            confidence=0.75, verification_status="UNVERIFIED"),
    ])
    assert status != "VERIFIED"


def test_source_with_one_genuinely_verified_field_is_verified():
    status, _ = _classify_source_status([
        _ev(field_name="legal_name", field_value="ACME WIDGETS PRIVATE LIMITED",
            confidence=0.60, verification_status="UNVERIFIED"),
        _ev(field_name="company_status", field_value="ACTIVE",
            confidence=0.90, verification_status="VERIFIED"),
    ])
    assert status == "VERIFIED"


# --- build_verification_summary --------------------------------------------- #
def test_general_web_not_verified_when_only_low_confidence_unverified_fields():
    evidences = [
        _ev(field_name="legal_name", field_value="ACME WIDGETS PRIVATE LIMITED",
            source_name="General Web", confidence=0.60, verification_status="UNVERIFIED"),
        _ev(field_name="business_activity", field_value="Software development services",
            source_name="General Web", confidence=0.60, verification_status="UNVERIFIED"),
    ]
    summary = build_verification_summary(evidences)
    assert summary["general_web"]["status"] != "VERIFIED"


def test_verified_summary_status_always_backed_by_a_verified_evidence():
    """Consistency invariant: any source reported VERIFIED has >=1 persisted
    evidence whose own verification_status == VERIFIED (or legacy: no status +
    confidence >= 0.70)."""
    evidences = [
        _ev(field_name="gst_status", field_value="ACTIVE", source_name="GST Portal",
            confidence=0.95, verification_status="VERIFIED"),
        _ev(field_name="legal_name", field_value="ACME WIDGETS PRIVATE LIMITED",
            source_name="General Web", confidence=0.60, verification_status="UNVERIFIED"),
        _ev(field_name="company_status", field_value="ACTIVE", source_name="Third-Party Source",
            confidence=0.75, verification_status="UNVERIFIED"),
    ]
    summary = build_verification_summary(evidences)
    by_source = {"gst": "GST Portal", "general_web": "General Web", "third_party": "Third-Party Source"}
    for cat, data in summary.items():
        if data["status"] != "VERIFIED":
            continue
        src = by_source.get(cat)
        assert any(_evidence_is_verified(e) for e in evidences if e.source_name == src), cat

    assert summary["gst"]["status"] == "VERIFIED"
    assert summary["general_web"]["status"] != "VERIFIED"
    assert summary["third_party"]["status"] != "VERIFIED"


def test_candidate_entities_rows_do_not_verify_or_count_toward_a_source():
    """Discovery *leads* (candidate_entities) -- including the discovery agent's
    own high-confidence output -- are not a source's verification of a fact and
    must not make a source VERIFIED or inflate its evidence_count."""
    evidences = [
        _ev(field_name="candidate_entities", field_value='[{"name": "INFOSYS LIMITED"}]',
            source_name="discovery_agent", confidence=0.95, verification_status="UNVERIFIED"),
        _ev(field_name="candidate_entities", field_value='[{"name": "INFOSYS LIMITED"}]',
            source_name="General Web", confidence=0.95, verification_status=None),
        _ev(field_name="legal_name", field_value="INFOSYS LIMITED",
            source_name="General Web", confidence=0.60, verification_status="UNVERIFIED"),
    ]
    summary = build_verification_summary(evidences)
    assert summary["general_web"]["status"] != "VERIFIED"
    assert summary["general_web"]["evidence_count"] == 1  # only the legal_name row


def test_source_with_only_candidate_entities_is_not_verified():
    evidences = [
        _ev(field_name="candidate_entities", field_value='[{"name": "ACME"}]',
            source_name="General Web", confidence=0.95, verification_status="VERIFIED"),
    ]
    summary = build_verification_summary(evidences)
    assert summary["general_web"]["status"] != "VERIFIED"
    assert summary["general_web"]["evidence_count"] == 0
