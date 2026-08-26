import uuid
import json
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.investigation import Investigation
from app.services.evidence import get_evidences_for_investigation
from app.services.report import generate_investigation_report
from app.services.risk_analysis import analyze_investigation


def validate_report(
    db: Session,
    investigation_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    Validates a structured report against persisted database evidences and deterministic risk scoring outcomes.
    """
    # 1. Load the Investigation
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise ValueError(f"Investigation with ID {investigation_id} not found.")

    # 2. Retrieve persisted Evidence
    db_evidences = get_evidences_for_investigation(db, investigation_id)
    db_evidence_ids = {ev.research_result_id for ev in db_evidences}

    # 3. Retrieve structured report
    report = generate_investigation_report(db, investigation_id)

    issues = []
    evidence_coverage = 1.0
    score_verified = True
    entity_verified = True

    # Check A: Evidence Coverage & Traceability
    findings = report.get("major_findings") or []
    valid_findings = 0
    total_findings = len(findings)

    for finding in findings:
        desc = finding.get("description") or ""
        evidence_ids = finding.get("evidence_ids")
        if not evidence_ids or not isinstance(evidence_ids, list):
            issues.append({
                "type": "MISSING_EVIDENCE",
                "finding": f"Finding '{desc}' has no supporting evidence IDs list."
            })
            continue

        valid_refs = True
        for ev_id in evidence_ids:
            if ev_id not in db_evidence_ids:
                valid_refs = False
                issues.append({
                    "type": "MISSING_EVIDENCE",
                    "finding": f"Finding '{desc}' references non-existent evidence ID '{ev_id}'."
                })

        if valid_refs:
            valid_findings += 1

        # Check E: Language Safety
        desc_lower = desc.lower()
        for word in ["fraud", "scam", "fake", "fraudster"]:
            if word in desc_lower:
                supported = False
                for ev_id in evidence_ids:
                    ev = next((x for x in db_evidences if x.research_result_id == ev_id), None)
                    if ev:
                        val_str = str(ev.field_value).lower()
                        src_str = str(ev.source_name).lower()
                        if word in val_str or word in src_str:
                            supported = True
                            break
                if not supported:
                    issues.append({
                        "type": "REPORT_WORDING",
                        "finding": f"Finding description uses term '{word}' without support in associated evidence."
                    })

        # Check F: Contradiction / Unsupported Claim Check
        code = finding.get("code")
        if code == "GST_INACTIVE":
            for ev in db_evidences:
                if ev.field_name == "gst_status":
                    val_clean = str(ev.field_value).strip().lower()
                    if "active" in val_clean and "inactive" not in val_clean:
                        issues.append({
                            "type": "UNSUPPORTED_CLAIM",
                            "finding": f"Finding GST inactive contradicts actual GST status evidence: '{ev.field_value}'."
                        })

    if total_findings > 0:
        evidence_coverage = valid_findings / total_findings

    # Check B: Entity Verification
    entity = report.get("entity") or {}
    entity_confidence = report.get("entity_confidence")
    
    # Entity must be non-empty and confidence >= 0.5 (TRD threshold)
    if not entity or entity.get("business_name") is None:
        entity_verified = False
        issues.append({
            "type": "WRONG_ENTITY",
            "finding": "Resolved entity information is empty."
        })
    elif entity_confidence is not None and entity_confidence < 0.5:
        entity_verified = False
        issues.append({
            "type": "WRONG_ENTITY",
            "finding": f"Resolved entity confidence {entity_confidence} is below threshold 0.5."
        })

    # Check C & D: Score & Risk consistency
    engine_analysis = analyze_investigation(db, investigation_id)
    engine_score = engine_analysis["overall_risk"]["score"]
    report_score = report["overall_risk"]["score"]
    if report_score != engine_score:
        score_verified = False
        issues.append({
            "type": "WRONG_RISK_SCORE",
            "finding": f"Report risk score {report_score} does not match Risk Engine output {engine_score}."
        })

    status_str = "PASS" if not issues else "FAIL"

    # Update persisted report QA status
    from app.models.report import Report
    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation_id)
        .order_by(Report.version.desc())
        .first()
    )
    if latest_report:
        latest_report.qa_status = status_str
        db.commit()

    return {
        "status": status_str,
        "issues": issues,
        "evidence_coverage": evidence_coverage,
        "score_verified": score_verified,
        "entity_verified": entity_verified,
    }
