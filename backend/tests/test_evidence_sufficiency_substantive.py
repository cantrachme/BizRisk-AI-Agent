"""
Evidence-sufficiency gate: a single low-information availability/status field
must not be enough to declare an investigation "sufficiently evidenced".

Root cause: `mca_status` / `epfo_status` / `website_status` only record that a
source page loaded (app/agents/browser.py extracts all three through the same
"page successfully retrieved" -> AVAILABLE / else UNAVAILABLE branch) -- never
any fact about the company. Before this fix, a single one of these at
confidence >= 0.70 (or explicitly VERIFIED) alone cleared the sufficiency gate,
letting a 0/LOW "no risk" conclusion be reached without any substantive
identity/registry evidence.

Fix: the sufficiency gate now additionally requires that at least one verified
factual record is *substantive*, i.e. its field_name is not one of the known
page-availability-only fields (LOW_INFORMATION_AVAILABILITY_FIELDS). Everything
else about the gate -- the >= 0.70 / explicit VERIFIED confidence semantics,
candidate_entities being excluded, and rule scoring once evidence is
sufficient -- is unchanged.

Generic and deterministic: no company-specific values, no company hardcoded.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.graph.state import ResearchResult
from app.risk.engine import (
    LOW_INFORMATION_AVAILABILITY_FIELDS,
    VERIFIED_EVIDENCE_CONFIDENCE,
    calculate_risk_analysis,
)


def _r(rid, fn, fv, src, conf=0.9, vs="VERIFIED", at="2026-09-04T00:00:00Z"):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name=src,
        source_url=None, retrieved_at=at, confidence=conf, verification_status=vs,
    )


# --------------------------------------------------------------------------- #
# 1. single epfo_status=AVAILABLE (high confidence) -> insufficient
# --------------------------------------------------------------------------- #
def test_single_epfo_status_available_is_insufficient():
    out = calculate_risk_analysis([_r("R1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9)])
    assert out["insufficient_evidence"] is True
    assert out["overall_risk"]["score"] is None
    assert out["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"
    assert out["risk_signals"] == []


def test_single_epfo_status_available_explicitly_verified_is_still_insufficient():
    # Confidence alone (or an explicit VERIFIED tag) must not make a weak field
    # sufficient -- this stays insufficient even below/at/above the 0.70 bar.
    for conf in (0.5, VERIFIED_EVIDENCE_CONFIDENCE, 0.99):
        out = calculate_risk_analysis([
            _r("R1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=conf, vs="VERIFIED"),
        ])
        assert out["insufficient_evidence"] is True, conf


# --------------------------------------------------------------------------- #
# 2. another similarly weak status/availability field alone -> insufficient
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_name", sorted(LOW_INFORMATION_AVAILABILITY_FIELDS))
def test_single_weak_availability_field_is_insufficient(field_name):
    out = calculate_risk_analysis([_r("R1", field_name, "AVAILABLE", "Some Portal", conf=0.95)])
    assert out["insufficient_evidence"] is True, field_name
    assert out["overall_risk"]["score"] is None, field_name


def test_multiple_weak_availability_fields_together_still_insufficient():
    # Several independent page-loaded signals still say nothing about the
    # company -- stacking weak fields does not manufacture substance.
    out = calculate_risk_analysis([
        _r("R1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
        _r("R2", "website_status", "AVAILABLE", "Company Website", conf=0.9),
        _r("R3", "mca_status", "AVAILABLE", "MCA Portal", conf=0.9),
    ])
    assert out["insufficient_evidence"] is True
    assert out["overall_risk"]["score"] is None


# --------------------------------------------------------------------------- #
# 3. adequate substantive evidence -> sufficient, existing scoring unaffected
# --------------------------------------------------------------------------- #
def test_substantive_evidence_is_sufficient_and_scores_normally():
    # A single substantive, verified field is enough on its own...
    clean = calculate_risk_analysis([_r("R1", "company_status", "ACTIVE", "MCA Portal", conf=0.9)])
    assert clean["insufficient_evidence"] is False
    assert clean["overall_risk"]["score"] == 0
    assert clean["overall_risk"]["level"] == "LOW"

    # ...and weak availability fields can ride alongside substantive evidence
    # without breaking sufficiency or altering the score.
    mixed = calculate_risk_analysis([
        _r("R1", "legal_name", "EXAMPLE ENTERPRISES PRIVATE LIMITED", "GST Portal", conf=0.9),
        _r("R2", "gst_status", "cancelled", "GST Portal", conf=0.9),
        _r("R3", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
    ])
    assert mixed["insufficient_evidence"] is False
    codes_weights = [(s["code"], s["risk_weight"]) for s in mixed["risk_signals"]]
    assert codes_weights == [("GST_INACTIVE", 30)]
    assert mixed["overall_risk"]["score"] == 30


# --------------------------------------------------------------------------- #
# 4. clearly adverse company_status still produces COMPANY_STATUS_ADVERSE even
#    as the sole piece of evidence (limited overall coverage)
# --------------------------------------------------------------------------- #
def test_adverse_company_status_alone_still_produces_signal():
    out = calculate_risk_analysis([_r("R1", "company_status", "STRUCK OFF", "MCA Portal", conf=0.9)])
    assert out["insufficient_evidence"] is False
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("COMPANY_STATUS_ADVERSE", 35)]
    assert out["overall_risk"]["score"] == 35
    assert out["overall_risk"]["level"] == "MODERATE"


# --------------------------------------------------------------------------- #
# 5. existing clean-investigation fixtures do not regress
# --------------------------------------------------------------------------- #
def test_infosys_shape_style_fixture_unaffected():
    # Same evidence shape as test_risk_assessment_not_falsely_neutral.py's
    # _infosys_shape(): several ACTIVE company_status records plus weak
    # epfo_status/website_status availability signals alongside consistent
    # legal_name/registered_address. company_status/legal_name/address are
    # substantive, so sufficiency (and the correct score-0/LOW conclusion) is
    # unaffected by excluding the weak fields.
    out = calculate_risk_analysis([
        _r("c1", "company_status", "ACTIVE", "QuickCompany", conf=0.8),
        _r("c2", "company_status", "ACTIVE", "Tofler", conf=0.75),
        _r("e1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
        _r("w1", "website_status", "AVAILABLE", "Company Website", conf=0.85),
        _r("n1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "Tofler", conf=0.75),
        _r("n2", "legal_name", "Example Enterprises Limited", "Zauba Corp", conf=0.75),
        _r("a1", "registered_address", "12 MG Road, Sector 5, Example City 560100", "Tofler", conf=0.75),
        _r("a2", "registered_address", "12 MG Road, Sector 5, Example City 560100", "Zauba Corp", conf=0.75),
    ])
    assert out["insufficient_evidence"] is False
    assert out["overall_risk"]["score"] == 0
    assert out["overall_risk"]["level"] == "LOW"
    assert out["risk_signals"] == []


def test_only_low_confidence_factual_evidence_still_insufficient():
    # Unrelated to this fix, but must remain true: sub-0.70, non-VERIFIED
    # factual evidence stays insufficient regardless of field substance.
    out = calculate_risk_analysis([_r("R1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "General Web",
                                       conf=0.6, vs="UNVERIFIED")])
    assert out["insufficient_evidence"] is True


# --------------------------------------------------------------------------- #
# 6. missing sources are neutral, never negative
# --------------------------------------------------------------------------- #
def test_missing_weak_availability_sources_do_not_become_adverse():
    # An investigation with only substantive evidence and no
    # mca_status/epfo_status/website_status at all must score identically to
    # one that also has those fields present (as AVAILABLE) -- their absence
    # is neutral, not a risk signal, and their presence adds no risk either.
    without_weak = calculate_risk_analysis([
        _r("R1", "company_status", "ACTIVE", "MCA Portal", conf=0.9),
        _r("R2", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "MCA Portal", conf=0.9),
    ])
    with_weak = calculate_risk_analysis([
        _r("R1", "company_status", "ACTIVE", "MCA Portal", conf=0.9),
        _r("R2", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "MCA Portal", conf=0.9),
        _r("R3", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
        _r("R4", "website_status", "AVAILABLE", "Company Website", conf=0.9),
    ])
    assert without_weak["insufficient_evidence"] is False
    assert with_weak["insufficient_evidence"] is False
    assert without_weak["overall_risk"] == with_weak["overall_risk"]
    assert without_weak["risk_signals"] == with_weak["risk_signals"] == []


def test_unavailable_weak_field_does_not_make_investigation_adverse():
    # A weak field reporting UNAVAILABLE (source could not be reached) must
    # not be treated as negative evidence about the company -- it simply
    # cannot establish sufficiency, same as AVAILABLE.
    out = calculate_risk_analysis([_r("R1", "mca_status", "UNAVAILABLE", "MCA Portal", conf=0.9)])
    assert out["insufficient_evidence"] is True
    assert out["overall_risk"]["score"] is None
    assert out["risk_signals"] == []
