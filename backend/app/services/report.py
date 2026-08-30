import uuid
import json
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.investigation import Investigation
from app.services.evidence import get_evidences_for_investigation
from app.services.risk_analysis import analyze_investigation
from app.agents.intake import IntakeAgent
from app.entity_resolution.resolver import resolve_entity


def generate_recommendation(score: int) -> str:
    """
    Generates a deterministic risk recommendation based on the overall risk score.
    """
    if score >= 61:
        return "High risk detected. Additional manual verification is highly recommended before proceeding."
    elif score >= 31:
        return "Moderate risk detected. Additional verification recommended for mismatching fields."
    else:
        return "Low risk detected. Standard compliance criteria met."


def generate_investigation_report(
    db: Session,
    investigation_id: uuid.UUID,
    llm=None,
    prompt_version: str = "v1",
) -> Dict[str, Any]:
    from app.core.llm import get_llm_provider
    from app.core.prompts import load_prompt
    from app.core.config import get_settings
    resolved_llm = llm or get_llm_provider(temperature=0.2)
    prompt = load_prompt("report", prompt_version)
    settings = get_settings()
    """
    Generates a structured, evidence-backed report for the given investigation_id.
    """
    from app.db.session import db_lock

    with db_lock:
        # 1. Load the Investigation
        investigation = db.get(Investigation, investigation_id)
        if not investigation:
            raise ValueError(f"Investigation with ID {investigation_id} not found.")

        # 2. Retrieve persisted Evidence objects
        evidences = get_evidences_for_investigation(db, investigation_id)

        # 3. Perform Entity Resolution on the fly
        raw_input = json.loads(investigation.input_data)
        normalized_input = IntakeAgent().process(raw_input)

        candidates = []
        for ev in evidences:
            if ev.field_name == "candidate_entities":
                if ev.confidence < 0.5:
                    continue
                try:
                    val = json.loads(ev.field_value)
                    if isinstance(val, list):
                        candidates.extend(val)
                except Exception:
                    pass

        resolution = resolve_entity(normalized_input, candidates)
        entity = resolution["entity"] or {}
        if entity and "business_name" not in entity and "name" in entity:
            entity["business_name"] = entity["name"]
        entity_confidence = resolution["confidence"] or 0.0

        # 4. Obtain the current deterministic risk analysis
        analysis = analyze_investigation(db, investigation_id)

        # 5. Map risk signals to findings
        major_findings = []
        for sig in analysis["risk_signals"]:
            major_findings.append({
                "code": sig["code"],
                "category": sig["category"],
                "severity": sig["severity"],
                "description": sig["description"],
                "evidence_ids": sig["evidence_ids"],
                "confidence": sig["confidence"],
                "risk_weight": sig["risk_weight"],
            })

        # 6. Map evidence to summary
        evidence_summary = []
        # Sort evidences by research_result_id deterministically
        sorted_evidences = sorted(evidences, key=lambda x: x.research_result_id)
        for ev in sorted_evidences:
            val = ev.field_value
            try:
                val_loaded = json.loads(val)
                if isinstance(val_loaded, (list, dict)):
                    val = val_loaded
            except (ValueError, TypeError):
                pass

            retrieved_timestamp_str = ""
            if ev.retrieved_timestamp:
                retrieved_dt = ev.retrieved_timestamp
                if retrieved_dt.tzinfo is None:
                    retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
                retrieved_timestamp_str = retrieved_dt.isoformat()

            evidence_summary.append({
                "evidence_id": ev.research_result_id,
                "task_id": ev.task_id,
                "field_name": ev.field_name,
                "field_value": val,
                "source_name": ev.source_name,
                "source_url": ev.source_url,
                "retrieved_at": retrieved_timestamp_str,
                "confidence": ev.confidence,
            })

        # 7. Construct the report dict
        report_dict = {
            "entity": entity,
            "entity_confidence": entity_confidence,
            "overall_risk": {
                "score": analysis["overall_risk"]["score"],
                "level": analysis["overall_risk"]["level"],
            },
            "category_scores": analysis["category_scores"],
            "major_findings": major_findings,
            "positive_findings": [],
            "unverified_information": [],
            "recommendation": generate_recommendation(analysis["overall_risk"]["score"]),
            "evidence_summary": evidence_summary,
            "meta": {
                "rule_version": "1.0.0",
                "report_version": "1.0.0",
                "prompt_version": {
                    "intake": "v1",
                    "discovery": "v1",
                    "planner": "v1",
                    "entity_resolution": "v1",
                    "risk_analysis": "v1",
                    "report": prompt_version,
                    "qa": "v1",
                },
                "model_version": resolved_llm.model if hasattr(resolved_llm, "model") else settings.llm_model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        }

        # 8. Persist the report
        from app.models.report import Report
        from app.risk.engine import RISK_RULE_VERSION

        report_dict["meta"]["rule_version"] = RISK_RULE_VERSION

        try:
            latest_report = (
                db.query(Report)
                .filter(Report.investigation_id == investigation_id)
                .order_by(Report.version.desc())
                .first()
            )
            db_version = (latest_report.version + 1) if latest_report else 1

            report_dict["meta"]["report_version"] = str(db_version)

            new_report = Report(
                investigation_id=investigation_id,
                version=db_version,
                report_json=json.dumps(report_dict),
                qa_status="PENDING",
                created_at=datetime.now(timezone.utc),
            )
            db.add(new_report)
            db.commit()
            db.refresh(new_report)
        except Exception:
            db.rollback()
            raise

    return report_dict

