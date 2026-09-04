import uuid
import json
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.investigation import Investigation
from app.services.evidence import get_evidences_for_investigation
from app.services.report import generate_investigation_report
from app.services.risk_analysis import analyze_investigation

def validate_report_grounding(
    db: Session,
    investigation_id: uuid.UUID,
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validates that report findings containing evidence references point to valid Evidence IDs
    belonging to the same investigation.
    """
    from app.services.evidence import get_evidences_for_investigation
    from app.models.evidence import Evidence

    db_evidences = get_evidences_for_investigation(db, investigation_id)

    # Build the evidence index only from evidence belonging to this
    # investigation. This also avoids an unscoped Evidence lookup.
    db_evidence_by_result_id = {
        ev.research_result_id: ev
        for ev in db_evidences
        if ev.research_result_id
    }

    issues = []
    findings = report.get("major_findings") or []
    valid_findings = 0
    total_findings = len(findings)

    for finding in findings:
        desc = finding.get("description") or ""
        evidence_ids = finding.get("evidence_ids")

        # 1. Missing evidence_ids list or non-list
        if evidence_ids is None or not isinstance(evidence_ids, list):
            issues.append({
                "type": "MISSING_EVIDENCE",
                "finding": f"Finding '{desc}' has no supporting evidence IDs list."
            })
            continue

        # 2. Required evidence references missing (finding exists but list is empty)
        if not evidence_ids and finding.get("code"):
            issues.append({
                "type": "MISSING_EVIDENCE",
                "finding": f"Finding '{desc}' has required evidence references missing."
            })
            continue

        valid_refs = True
        for ev_id in evidence_ids:
            # 3. First require the evidence to belong to this investigation.
            ev_in_db = db_evidence_by_result_id.get(ev_id)

            if ev_in_db:
                continue

            # 4. If it is not in this investigation, distinguish a genuinely
            #    missing evidence ID from an ID belonging to another investigation.
            other_ev = (
                db.query(Evidence)
                .filter(Evidence.research_result_id == ev_id)
                .first()
            )

            valid_refs = False

            if other_ev:
                issues.append({
                    "type": "MISSING_EVIDENCE",
                    "finding": (
                        f"Finding '{desc}' references evidence ID '{ev_id}' "
                        "belonging to another investigation."
                    )
                })
            else:
                issues.append({
                    "type": "MISSING_EVIDENCE",
                    "finding": (
                        f"Finding '{desc}' references non-existent evidence ID "
                        f"'{ev_id}'."
                    )
                })

        if valid_refs:
            valid_findings += 1

    evidence_coverage = (valid_findings / total_findings) if total_findings > 0 else 1.0
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "evidence_coverage": evidence_coverage,
    }


def validate_report(
    db: Session,
    investigation_id: uuid.UUID,
    llm=None,
    prompt_version: str = "v1",
) -> Dict[str, Any]:
    from app.core.llm import get_llm_provider
    from app.core.prompts import load_prompt
    resolved_llm = llm or get_llm_provider(temperature=0.0)
    prompt = load_prompt("qa", prompt_version)
    """
    Validates a structured report against persisted database evidences and deterministic risk scoring outcomes.
    """
    # 1. Load the Investigation
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise ValueError(f"Investigation with ID {investigation_id} not found.")

    # 2. Retrieve persisted Evidence
    db_evidences = get_evidences_for_investigation(db, investigation_id)

    # 3. Retrieve structured report from DB or generate if not exists
    from app.models.report import Report
    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation_id)
        .order_by(Report.version.desc())
        .first()
    )
    if latest_report:
        report = json.loads(latest_report.report_json)
    else:
        report = generate_investigation_report(db, investigation_id)

    issues = []
    score_verified = True
    entity_verified = True

    # Check A: Evidence Coverage & Grounding Validation
    grounding_res = validate_report_grounding(db, investigation_id, report)
    issues.extend(grounding_res["issues"])
    evidence_coverage = grounding_res["evidence_coverage"]

    # Now run the remaining checks (Check E & F, and entity/score verification)
    findings = report.get("major_findings") or []
    for finding in findings:
        desc = finding.get("description") or ""
        evidence_ids = finding.get("evidence_ids") or []

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

    # Check B: Entity Verification & Identifier Preservation
    entity = report.get("entity") or {}
    entity_confidence = report.get("entity_confidence")
    entity_name = entity.get("business_name") or entity.get("name")
    is_insufficient = report.get("overall_risk", {}).get("level") == "INSUFFICIENT_EVIDENCE"

    raw_input_dict = {}
    if investigation and investigation.input_data:
        try:
            raw_input_dict = json.loads(investigation.input_data)
        except Exception:
            pass

    # Verify user-supplied identifiers were not dropped
    for id_field in ["gstin", "cin", "epfo_code"]:
        val = raw_input_dict.get(id_field)
        if val and str(val).strip().lower() not in {"invalid", "none", "null", "n/a", ""}:
            if not entity.get(id_field):
                entity_verified = False
                issues.append({
                    "type": "WRONG_ENTITY",
                    "finding": f"User-supplied {id_field.upper()} was dropped from resolved entity."
                })

    # Entity must have a name
    if not entity or not entity_name:
        entity_verified = False
        issues.append({
            "type": "WRONG_ENTITY",
            "finding": "Resolved entity information is empty."
        })
    elif not is_insufficient and entity_confidence is not None and entity_confidence < 0.5:
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

    # The deterministic checks above are the ONLY authority for PASS/FAIL.
    status_str = "PASS" if not issues else "FAIL"
    import logging
    logging.getLogger("bizrisk.observability").error(f"[QA_ISSUES_DIAG] {issues}")

    # Optional LLM QA reasoning — advisory only. It cannot change status_str and
    # cannot touch the risk score; it only contributes human-readable notes.
    advisory_notes: list = []
    try:
        from app.core.llm import run_structured_sync
        from app.schemas.agent_outputs import QAReasoning

        qa_context = {
            "deterministic_status": status_str,
            "deterministic_issues": issues,
            "evidence_coverage": evidence_coverage,
            "score_verified": score_verified,
            "entity_verified": entity_verified,
            "risk_score_readonly": report.get("overall_risk", {}).get("score"),
            "risk_level_readonly": report.get("overall_risk", {}).get("level"),
            "major_findings": report.get("major_findings") or [],
        }
        reasoning = run_structured_sync(
            resolved_llm,
            f"{prompt}\n\nReview this due-diligence report QA context and surface any additional "
            f"evidence-grounding, contradiction, or wording concerns as short advisory notes. "
            f"Do NOT decide PASS/FAIL and do NOT output a risk score — both are fixed.\n\n"
            f"{json.dumps(qa_context, default=str)}",
            QAReasoning,
            system_instruction=(
                "You are an advisory QA reviewer for business due-diligence reports. You never "
                "decide PASS/FAIL and never produce or alter a numeric risk score. Output only "
                "concise advisory notes about evidence grounding, contradictions, or unsafe wording."
            ),
        )
        if reasoning is not None and reasoning.advisory_notes:
            advisory_notes = [str(n) for n in reasoning.advisory_notes][:10]
    except Exception:
        advisory_notes = []

    # Update persisted report QA status for this specific version being evaluated
    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation_id)
        .order_by(Report.version.desc())
        .first()
    )
    if latest_report:
        try:
            latest_report.qa_status = status_str
            db.commit()
        except Exception:
            db.rollback()
            raise


    return {
        "status": status_str,
        "issues": issues,
        "evidence_coverage": evidence_coverage,
        "score_verified": score_verified,
        "entity_verified": entity_verified,
        "advisory_notes": advisory_notes,
    }
