"""
Falcon Ebiz third-party research source.

Proves it is (1) registered in the source registry, (2) dynamically scheduled by
the planner and its URL dynamically constructed from entity data, (3) routed
through the existing HTTP -> browser research + entity-resolution + evidence
pipeline, producing persisted evidence only for an accessible, target-matching
page while blocked/wrong-entity attempts remain diagnostics only.

No company, GSTIN, CIN, or company-specific URL is hardcoded here.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.research.source_registry import SourceType, source_registry
from app.agents.planner import PlannerAgent
from app.agents.browser import BrowserResearchAgent
from app.services.report import build_verification_summary


FALCON_NAME = "falconebiz.com"
# Arbitrary, unseeded entity — never referenced anywhere in source code.
ARB_NAME = "Northwind Trading And Logistics Private Limited"
ARB_CIN = "U63030DL2011PTC221145"
ARB_GSTIN = "07AABCN1234M1Z8"


# --------------------------------------------------------------------------- #
# 1. Registered as a supported third-party source
# --------------------------------------------------------------------------- #
def test_falconebiz_is_registered_third_party_source():
    for key in (FALCON_NAME, "Falcon Ebiz", "falconebiz"):
        meta = source_registry.get_source(key)
        assert meta is not None, key
        assert meta.source_type == SourceType.THIRD_PARTY_REGISTRY
        assert meta.authority_tier == 3
        assert 0.0 < meta.default_confidence <= 0.80
        assert meta.base_url and "falconebiz.com" in meta.base_url

    tp = {s.name for s in source_registry.list_sources(task_type="THIRD_PARTY_RESEARCH", enabled_only=True)}
    assert FALCON_NAME in tp
    # scoped to third-party research only, not the government/MCA verification path
    mca = {s.name for s in source_registry.list_sources(task_type="MCA_VERIFICATION", enabled_only=True)}
    assert FALCON_NAME not in mca


# --------------------------------------------------------------------------- #
# 2. Dynamic URL discovery/construction from entity data
# --------------------------------------------------------------------------- #
def test_falconebiz_url_is_constructed_dynamically_from_entity_data():
    meta = source_registry.get_source(FALCON_NAME)

    # name + CIN present -> resolves a concrete company page built from slug+CIN
    target = f"{ARB_NAME} {ARB_CIN} {ARB_GSTIN}"
    urls = meta.get_candidate_urls(target, task_type="THIRD_PARTY_RESEARCH")
    assert urls, "expected candidate URLs"
    primary = urls[0]
    assert primary.startswith("https://www.falconebiz.com/")
    assert ARB_CIN in primary
    assert "northwind-trading-and-logistics" in primary.lower()
    assert meta.resolve_target_url(target, "THIRD_PARTY_RESEARCH") == primary

    # name only (no identifiers) -> still only falconebiz.com URLs, degraded to
    # bare-name / search / landing (to be rejected downstream by the entity gate)
    name_only = meta.get_candidate_urls("Some Unlisted Venture LLP", "THIRD_PARTY_RESEARCH")
    assert name_only and all(u.startswith("https://www.falconebiz.com") for u in name_only)

    # the browser agent's own resolver reaches the same dynamic URL
    from app.graph.state import ResearchTask
    task = ResearchTask(
        task_id="T1", task_type="THIRD_PARTY_RESEARCH", target=target,
        objective="x", required_fields=["legal_name"], priority=2,
        preferred_sources=[FALCON_NAME], fallback_sources=[],
    )
    resolved = BrowserResearchAgent._resolve_url(task, FALCON_NAME, meta.base_url)
    assert resolved and "falconebiz.com" in resolved and ARB_CIN in resolved


# --------------------------------------------------------------------------- #
# 3. Planner schedules a Falcon Ebiz THIRD_PARTY_RESEARCH task dynamically
# --------------------------------------------------------------------------- #
def _state(raw):
    return {
        "investigation_id": str(uuid.uuid4()),
        "raw_input": raw, "normalized_input": raw,
        "pending_tasks": [], "completed_tasks": [], "failed_tasks": [], "results": [],
        "status": "IN_PROGRESS", "planner_loop_count": 0,
    }


def test_planner_schedules_falconebiz_dynamically():
    tasks = PlannerAgent().plan(_state({"business_name": ARB_NAME, "cin": ARB_CIN}))
    tp = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]
    falcon = [t for t in tp if FALCON_NAME in (t.preferred_sources or [])]
    assert len(falcon) == 1
    assert falcon[0].preferred_sources == [FALCON_NAME]
    assert ARB_CIN in falcon[0].target and "Northwind" in falcon[0].target
    # the other registered directory sources are still scheduled alongside it
    assert {"zaubacorp.com", "tofler.in", "quickcompany.in", "instafinancials.com"} <= {
        t.preferred_sources[0] for t in tp
    }


# --------------------------------------------------------------------------- #
# 4. Evidence pipeline: accessible + target-matching page -> persisted evidence
# --------------------------------------------------------------------------- #
def _falcon_task():
    from app.graph.state import ResearchTask
    return ResearchTask(
        task_id="TASK-FALCON",
        task_type="THIRD_PARTY_RESEARCH",
        target=f"{ARB_NAME} {ARB_CIN}",
        objective="Search Falcon Ebiz",
        required_fields=["legal_name", "company_status", "registered_address"],
        priority=2,
        preferred_sources=[FALCON_NAME],
        fallback_sources=[],
    )


def test_falconebiz_matching_page_produces_persisted_grade_evidence():
    def fetcher(url):
        assert "falconebiz.com" in url
        # a real Falcon company page: legal name + matching CIN in the content
        return (
            f"<html><head><title>{ARB_NAME.upper()} HAVING CIN {ARB_CIN}</title></head>"
            f"<body><h1>{ARB_NAME}</h1>"
            f"<p>CIN: {ARB_CIN}</p>"
            f"<p>Company Status: Active</p>"
            f"<p>Registered Address: 14 Kirti Nagar Industrial Area, New Delhi 110015</p>"
            f"<p>Principal Business Activity: Freight transport by road</p></body></html>"
        )

    agent = BrowserResearchAgent(fetcher=fetcher)
    results = agent.execute(_falcon_task())

    graded = [r for r in results if r.confidence and r.confidence > 0]
    assert graded, "expected target-matching Falcon evidence"
    assert all(r.source_name == "Falcon Ebiz" for r in graded)
    names = {r.field_name: r for r in graded}
    assert "legal_name" in names
    assert names["legal_name"].verification_status in {"VERIFIED", "UNVERIFIED"}

    # such evidence categorises as third-party in the report, never general web
    summary = build_verification_summary(graded)
    assert summary["third_party"]["evidence_count"] >= 1
    assert summary["general_web"]["evidence_count"] == 0


def test_falconebiz_wrong_entity_page_yields_no_evidence_only_diagnostics():
    # Falcon directory landing page for a different entity — no CIN / name match.
    def fetcher(url):
        return (
            "<html><head><title>Falcon Ebiz Pvt Ltd | GST/Company API | Corporate Directory</title></head>"
            "<body><h1>Falcon Ebiz Pvt Ltd</h1><p>Verify GST and company data.</p></body></html>"
        )

    agent = BrowserResearchAgent(fetcher=fetcher)
    results = agent.execute(_falcon_task())

    graded = [r for r in results if r.confidence and r.confidence > 0]
    assert graded == [], f"wrong-entity Falcon page must not yield evidence, got {graded}"
    # rejected/zero-confidence rows are still returned as attempt diagnostics
    assert results, "attempt diagnostics should still be present"
    assert all((r.confidence or 0.0) == 0.0 for r in results)


def test_falconebiz_blocked_fetch_yields_no_evidence_only_diagnostics():
    def fetcher(url):
        raise ConnectionResetError("Connection reset by peer")

    agent = BrowserResearchAgent(fetcher=fetcher)
    results = agent.execute(_falcon_task())
    graded = [r for r in results if r.confidence and r.confidence > 0]
    assert graded == []
