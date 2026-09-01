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


def generate_recommendation(score: int | None) -> str:
    """
    Generates a deterministic risk recommendation based on the overall risk score.
    """
    if score is None:
        return "Based on available public evidence, a complete risk assessment could not be finalized because insufficient verified external registry evidence was accessible."
    if score >= 61:
        return "Based on available public evidence, high risk was detected. Additional manual compliance verification is strongly recommended before proceeding with this business partner."
    elif score >= 31:
        return "Based on available public evidence, moderate risk was detected. Specific field discrepancies or status limitations require manual verification."
    else:
        return "Based on available public evidence, low risk was detected. Standard regulatory and business verification criteria were met."


def _classify_source_status(source_evidences: list) -> tuple[str, str]:
    if not source_evidences:
        return "UNAVAILABLE", "No research evidence retrieved from this source."
    
    # Check if any evidence is CAPTCHA / BLOCKED / UNAVAILABLE
    has_captcha = any("CAPTCHA" in str(ev.field_value).upper() or "CAPTCHA" in str(ev.field_name).upper() for ev in source_evidences)
    if has_captcha:
        return "CAPTCHA_REQUIRED", "Source requires human verification or CAPTCHA challenge."

    has_blocked = any(str(ev.field_value).strip().upper() in {"BLOCKED", "SOURCE_UNAVAILABLE", "TIMEOUT"} for ev in source_evidences)
    if has_blocked:
        return "BLOCKED", "Source access was blocked or timed out."

    has_not_found = any(str(ev.field_value).strip().upper() in {"NOT_FOUND", "NONE"} for ev in source_evidences)
    has_verified = any(ev.confidence >= 0.70 and str(ev.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE", "BLOCKED"} for ev in source_evidences)

    if has_verified:
        return "VERIFIED", "Evidence obtained and verified against target entity."
    elif has_not_found:
        return "NOT_FOUND", "Target entity record not found in this registry."
    
    return "UNAVAILABLE", "Source information unavailable or unverified."


def build_verification_summary(evidences: list) -> dict[str, dict[str, str]]:
    by_source_category = {
        "gst": [],
        "mca": [],
        "epfo": [],
        "official_website": [],
        "third_party": [],
        "general_web": [],
    }

    for ev in evidences:
        src = (ev.source_name or "").lower()
        if "gst" in src:
            by_source_category["gst"].append(ev)
        elif "mca" in src:
            by_source_category["mca"].append(ev)
        elif "epf" in src or "epfo" in src:
            by_source_category["epfo"].append(ev)
        elif "website" in src or "company" in src:
            by_source_category["official_website"].append(ev)
        elif "third_party" in src or "zauba" in src or "tofler" in src:
            by_source_category["third_party"].append(ev)
        else:
            by_source_category["general_web"].append(ev)

    summary = {}
    for cat, evs in by_source_category.items():
        status, details = _classify_source_status(evs)
        summary[cat] = {
            "status": status,
            "details": details,
            "evidence_count": len(evs),
        }

    return summary


def build_cross_source_consistency(evidences: list, entity: dict) -> list[dict[str, Any]]:
    fields_to_reconcile = [
        ("legal_name", "Legal Entity Name"),
        ("gstin", "GSTIN Identifier"),
        ("cin", "CIN Identifier"),
        ("registered_address", "Registered Office Address"),
        ("company_status", "Active Company Status"),
        ("business_activity", "Business Activity / Sector"),
        ("incorporation_date", "Incorporation Date"),
        ("website", "Official Domain / Website"),
    ]

    consistency_records = []

    for field_key, field_label in fields_to_reconcile:
        field_values = {}
        for ev in evidences:
            if ev.field_name == field_key and ev.confidence >= 0.50:
                val = str(ev.field_value).strip()
                if val.upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE", "BLOCKED"}:
                    field_values[ev.source_name] = val

        # Also compare against user-supplied target value if present
        target_val = entity.get(field_key)
        if target_val and str(target_val).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
            if "User Input" not in field_values:
                field_values["User Input"] = str(target_val).strip()

        if not field_values:
            consistency_records.append({
                "field": field_label,
                "field_key": field_key,
                "status": "UNAVAILABLE",
                "sources_compared": [],
                "analysis": "No verified evidence available from any source for comparison.",
            })
            continue

        unique_vals = list({v.upper(): v for v in field_values.values()}.values())
        sources_list = [{"source": src, "value": val} for src, val in field_values.items()]

        if len(unique_vals) == 1:
            status = "MATCH"
            analysis = f"All sources ({', '.join(field_values.keys())}) are fully consistent."
        else:
            STOPWORDS = {
                "AND", "THE", "OF", "IN", "FOR", "TO", "A", "AN", "PVT", "LTD", "LIMITED",
                "INC", "LLP", "CO", "COMPANY", "SERVICES", "SOLUTIONS", "DEVELOPMENT", "&", "-"
            }
            v1_words = {w.strip(",.;:") for w in unique_vals[0].upper().split() if w not in STOPWORDS and len(w) > 2}
            is_partial = False
            for other_v in unique_vals[1:]:
                v2_words = {w.strip(",.;:") for w in other_v.upper().split() if w not in STOPWORDS and len(w) > 2}
                if v1_words and v2_words:
                    intersection = v1_words.intersection(v2_words)
                    union = v1_words.union(v2_words)
                    if len(intersection) / len(union) >= 0.40:
                        is_partial = True
                        break
            
            if is_partial:
                status = "PARTIAL_MATCH"
                analysis = f"Minor formatting or text variations observed across sources: {', '.join(unique_vals)}."
            else:
                status = "CONFLICT"
                analysis = f"Conflicting data reported across sources: {'; '.join([f'{s}: {v}' for s, v in field_values.items()])}."

        consistency_records.append({
            "field": field_label,
            "field_key": field_key,
            "status": status,
            "sources_compared": sources_list,
            "analysis": analysis,
        })

    return consistency_records


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
        entity = resolution.get("entity")
        if not entity:
            entity = dict(normalized_input)
        else:
            merged = dict(normalized_input)
            for k, v in entity.items():
                if v and str(v).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                    merged[k] = v
            entity = merged
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

        # Collect source limitations and unverified info
        source_limitations = []
        unverified_info = []
        reason_codes = list(analysis.get("reason_codes") or [])

        for ev in sorted_evidences:
            val = ev.field_value
            val_str = str(val).strip().upper()
            if val_str in {"UNAVAILABLE", "SOURCE_UNAVAILABLE", "BLOCKED", "TIMEOUT"}:
                source_limitations.append({
                    "source": ev.source_name,
                    "field": ev.field_name,
                    "status": val_str,
                    "reason": f"External source {ev.source_name} was inaccessible or blocked during lookup.",
                })
            elif ev.confidence < 0.50 and val_str not in {"NOT_FOUND", "NONE"}:
                unverified_info.append({
                    "field": ev.field_name,
                    "value": val,
                    "source": ev.source_name,
                })

        is_insufficient = analysis.get("insufficient_evidence", False) or analysis["overall_risk"]["score"] is None
        if is_insufficient:
            if source_limitations and "AUTHORITATIVE_SOURCES_UNAVAILABLE" not in reason_codes:
                reason_codes.append("AUTHORITATIVE_SOURCES_UNAVAILABLE")
            if not entity.get("gstin") and not entity.get("cin"):
                if "NO_VERIFIED_ENTITY_RECORD" not in reason_codes:
                    reason_codes.append("NO_VERIFIED_ENTITY_RECORD")
            if "INSUFFICIENT_EVIDENCE" not in reason_codes:
                reason_codes.append("INSUFFICIENT_EVIDENCE")

        verification_summary = build_verification_summary(sorted_evidences)
        cross_source_consistency = build_cross_source_consistency(sorted_evidences, entity)

        # 7. Construct the report dict
        report_dict = {
            "entity": entity,
            "entity_confidence": entity_confidence,
            "overall_risk": {
                "score": analysis["overall_risk"]["score"],
                "level": analysis["overall_risk"]["level"],
            },
            "assessment_status": "INSUFFICIENT_EVIDENCE" if is_insufficient else "COMPLETED",
            "reason_codes": reason_codes,
            "verification_summary": verification_summary,
            "cross_source_consistency": cross_source_consistency,
            "source_limitations": source_limitations,
            "category_scores": analysis["category_scores"],
            "major_findings": major_findings,
            "positive_findings": [],
            "unverified_information": unverified_info,
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
