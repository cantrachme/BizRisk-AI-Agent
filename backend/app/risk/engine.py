import json
import os
import uuid
from typing import Any, Dict, List, Optional
import yaml
from sqlalchemy.orm import Session

from app.models.risk_signal import RiskSignal
from app.risk.rules import normalize_evidence, run_all_rules

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
RISK_RULE_VERSION = "1.0.0"

# Confidence at or above which a factual evidence record is treated as
# "verified" for the purpose of deciding whether the investigation has
# sufficient evidence to produce a numeric risk score. Mirrors the "verified"
# bar used by the report source-status classifier and the browser research
# agent. This is an evidence-sufficiency gate, not a risk score threshold.
VERIFIED_EVIDENCE_CONFIDENCE = 0.70

# Fields that only record whether a source page loaded / a record was found at
# a URL -- never a fact about the company itself. app/agents/browser.py
# extracts all three through the exact same branch ("Evidence page
# successfully retrieved" -> AVAILABLE, else UNAVAILABLE); none of them carries
# any name, status-of-registration, address, or identifier information. A
# single field like this being high-confidence or even explicitly VERIFIED
# only means a page was reachable, not that anything substantive about the
# company was confirmed, so it must never by itself satisfy the
# evidence-sufficiency gate. Generic and structural -- no company-specific
# values, and this does not affect scoring/rules once sufficiency is
# otherwise established.
LOW_INFORMATION_AVAILABILITY_FIELDS = {"mca_status", "epfo_status", "website_status"}

# verification_status values meaning the research layer already determined a
# record does not describe the target entity (wrong-entity page, unrelated
# search/navigation content, explicitly rejected). Production persistence
# (app/validation/research.py) already filters these before they reach the
# database, but the Risk Engine must not depend on that -- if called directly,
# or if upstream filtering ever regresses, contaminated evidence must still
# never participate in scoring. Defense-in-depth only: this does not change
# what gets persisted, just what this function will score.
EXCLUDED_VERIFICATION_STATUSES = {"REJECTED", "ENTITY_MISMATCH", "UNRELATED"}


class InsufficientEvidenceError(ValueError):
    """Exception raised when the minimum evidence requirements are not met."""
    pass


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def calculate_risk_analysis(
    evidences_raw: List[Any],
    investigation_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Computes overall risk score, risk level, category scores, and active risk signals.
    """
    print(f"\n[DIAGNOSTIC] === Risk Engine Evaluation Started ===", flush=True)
    if investigation_id:
        print(f"[DIAGNOSTIC] Investigation ID: {investigation_id}", flush=True)

    print(f"[DIAGNOSTIC] Number of raw evidence records retrieved: {len(evidences_raw)}", flush=True)
    for idx, ev in enumerate(evidences_raw, start=1):
        ev_id = getattr(ev, "research_result_id", None) or getattr(ev, "id", None) or (ev.get("id") if isinstance(ev, dict) else None)
        field_name = getattr(ev, "field_name", None) or (ev.get("field_name") if isinstance(ev, dict) else None)
        field_value = getattr(ev, "field_value", None) or (ev.get("field_value") if isinstance(ev, dict) else None)
        confidence = getattr(ev, "confidence", None) or (ev.get("confidence") if isinstance(ev, dict) else 0.0)
        source_name = getattr(ev, "source_name", None) or (ev.get("source_name") if isinstance(ev, dict) else None)
        val_repr = str(field_value)
        if len(val_repr) > 150:
            val_repr = val_repr[:147] + "..."
        print(f"[DIAGNOSTIC] Raw Record {idx}: ID={ev_id} | Field={field_name} | Value={val_repr} ({type(field_value).__name__}) | Confidence={confidence} | Source={source_name}", flush=True)

    config = load_config()
    rules_config = config.get("rules", {})
    levels_config = config.get("risk_levels", {})

    # Check minimum evidence requirement
    policy = config.get("minimum_evidence_policy", {})
    if policy.get("enabled", False):
        legal_sources_cfg = policy.get("legal_identity_sources", [])
        supporting_sources_cfg = policy.get("supporting_sources", [])
        min_legal = policy.get("min_legal_identity_sources", 1)
        min_supporting = policy.get("min_supporting_sources", 1)

        legal_found = set()
        supporting_found = set()

        for ev in evidences_raw:
            source_name = getattr(ev, "source_name", None)
            if source_name is None and isinstance(ev, dict):
                source_name = ev.get("source_name")
            confidence = getattr(ev, "confidence", 0.0)
            if isinstance(ev, dict):
                confidence = ev.get("confidence", 0.0)
            val = getattr(ev, "field_value", None) or (ev.get("field_value") if isinstance(ev, dict) else None)
            if confidence is None or float(confidence) < 0.5:
                continue
            if isinstance(val, str) and val.strip().upper() in {"NOT_FOUND", "UNAVAILABLE"}:
                continue
            verif_status = getattr(ev, "verification_status", None) or (ev.get("verification_status") if isinstance(ev, dict) else None)
            if verif_status in {"SOURCE_UNAVAILABLE", "NOT_FOUND"}:
                continue

            if source_name:
                if source_name in legal_sources_cfg:
                    legal_found.add(source_name)
                elif source_name in supporting_sources_cfg:
                    supporting_found.add(source_name)

        if len(legal_found) < min_legal or len(supporting_found) < min_supporting:
            raise InsufficientEvidenceError(
                f"Minimum evidence requirement not met. "
                f"Required: {min_legal} legal source(s) (found {len(legal_found)}: {list(legal_found)}) and "
                f"{min_supporting} supporting source(s) (found {len(supporting_found)}: {list(supporting_found)})."
            )

    # Normalize incoming raw evidence
    normalized_evs = [normalize_evidence(ev) for ev in evidences_raw]
    print(f"[DIAGNOSTIC] Number of normalized evidence records: {len(normalized_evs)}", flush=True)
    for idx, nev in enumerate(normalized_evs, start=1):
        nval_repr = str(nev.field_value)
        if len(nval_repr) > 150:
            nval_repr = nval_repr[:147] + "..."
        print(f"[DIAGNOSTIC] Normalized Record {idx}: ID={nev.id} | Field={nev.field_name} | Value={nval_repr} | Source={nev.source_name} | Confidence={nev.confidence}", flush=True)

    # Only validated, traceable evidence may participate in risk scoring.
    # Reject malformed evidence rather than allowing an ungrounded rule to affect score.
    validated_evs = []
    seen_ids = set()
    for ev in normalized_evs:
        evidence_id = str(ev.id).strip() if ev.id is not None else ""
        if not evidence_id or evidence_id in seen_ids:
            continue
        if not str(ev.source_name).strip() or not str(ev.field_name).strip():
            continue
        if not (0.5 <= float(ev.confidence) <= 1.0):
            continue
        if ev.field_value in [None, "", [], {}]:
            continue
        if isinstance(ev.field_value, str) and ev.field_value.strip().upper() in {"NOT_FOUND", "UNAVAILABLE"}:
            continue
        if getattr(ev, "verification_status", None) in {"SOURCE_UNAVAILABLE", "NOT_FOUND"}:
            continue
        if str(getattr(ev, "verification_status", "") or "").strip().upper() in EXCLUDED_VERIFICATION_STATUSES:
            continue
        seen_ids.add(evidence_id)
        validated_evs.append(ev)

    print(f"[DIAGNOSTIC] Number of validated evidence records passing confidence filter (>= 0.5): {len(validated_evs)}", flush=True)
    for idx, vev in enumerate(validated_evs, start=1):
        vval_repr = str(vev.field_value)
        if len(vval_repr) > 150:
            vval_repr = vval_repr[:147] + "..."
        print(f"[DIAGNOSTIC] Validated Record {idx}: ID={vev.id} | Field={vev.field_name} | Value={vval_repr} | Source={vev.source_name} | Confidence={vev.confidence}", flush=True)

    # Determine whether sufficient evidence exists before running deterministic rules.
    #
    # A numeric risk score must never rest on evidence that was not actually
    # verified. "Sufficient" therefore means: at least one factual (non
    # candidate_entities) record that is either high-confidence
    # (>= VERIFIED_EVIDENCE_CONFIDENCE, matching the report/browser "verified"
    # bar) or was explicitly marked verification_status == "VERIFIED" upstream.
    # candidate_entities are discovery leads, never sufficient on their own.
    #
    # That verified record must also be substantive: a page-availability-only
    # signal (LOW_INFORMATION_AVAILABILITY_FIELDS -- mca_status/epfo_status/
    # website_status) confirms a source loaded, not any fact about the company,
    # so it cannot by itself clear the gate no matter how high its confidence
    # is -- confidence alone must not make weak evidence sufficient. Any other
    # factual field (company_status, legal_name, gst_status, addresses,
    # identifiers, activity, dates, ...) is substantive and clears the gate on
    # its own, so a clearly adverse COMPANY_STATUS_ADVERSE finding still scores
    # even when it is the only evidence available.
    # Rule evaluation below is unchanged and still runs over the full
    # validated_evs set, so scoring/signal behaviour is preserved whenever
    # sufficient evidence exists.
    non_candidate_evs = [e for e in validated_evs if e.field_name != "candidate_entities"]
    verified_factual_evs = [
        e for e in non_candidate_evs
        if float(e.confidence) >= VERIFIED_EVIDENCE_CONFIDENCE
        or str(getattr(e, "verification_status", "") or "").strip().upper() == "VERIFIED"
    ]
    substantive_verified_evs = [
        e for e in verified_factual_evs
        if e.field_name not in LOW_INFORMATION_AVAILABILITY_FIELDS
    ]
    insufficient_evidence = len(substantive_verified_evs) == 0
    print(f"[DIAGNOSTIC] Validated Non-Candidate Evidence Count: {len(non_candidate_evs)} | Verified Factual Count: {len(verified_factual_evs)} | Substantive Verified Count: {len(substantive_verified_evs)} | Insufficient: {insufficient_evidence}", flush=True)

    if insufficient_evidence:
        overall_score = None
        risk_level = "INSUFFICIENT_EVIDENCE"
        category_scores = {}
        print(f"[DIAGNOSTIC] Final Risk Score: {overall_score}", flush=True)
        print(f"[DIAGNOSTIC] Final Risk Level: {risk_level}", flush=True)
        print(f"[DIAGNOSTIC] === Risk Engine Evaluation Ended (INSUFFICIENT EVIDENCE) ===\n", flush=True)
        return {
            "overall_risk": {
                "score": overall_score,
                "level": risk_level,
            },
            "category_scores": category_scores,
            "risk_signals": [],
            "insufficient_evidence": True,
        }

    # Evaluate deterministic rules only against validated evidence.
    triggered_rules = run_all_rules(validated_evs)
    print(f"[DIAGNOSTIC] Triggered Rules: {list(triggered_rules.keys())}", flush=True)

    active_signals = []
    active_weights = []
    category_weights: Dict[str, List[int]] = {
        "identity": [],
        "registration": [],
        "compliance": [],
        "consistency": [],
        "operational": [],
        "activity": [],
        "public_footprint": [],
    }

    # Map triggered rules to risk signals schema
    for code, run_res in triggered_rules.items():
        rule_cfg = rules_config.get(code, {})
        weight = rule_cfg.get("weight", 0)
        category = rule_cfg.get("category", "IDENTITY")
        severity = rule_cfg.get("severity", "MEDIUM")
        desc = run_res.get("description", rule_cfg.get("description", ""))

        active_weights.append(weight)
        category_lower = category.lower()
        if category_lower in category_weights:
            category_weights[category_lower].append(weight)

        active_signals.append({
            "code": code,
            "category": category,
            "severity": severity,
            "description": desc,
            "evidence_ids": run_res.get("evidence_ids", []),
            "confidence": run_res.get("confidence", 1.0),
            "risk_weight": weight,
        })

    # Overall score calculation: min(sum(active_signal_weights), 100)
    overall_score = min(sum(active_weights), 100)
    print(f"[DIAGNOSTIC] Score aggregation sum: {sum(active_weights)} | Capped: {overall_score}", flush=True)

    # Risk level classification
    risk_level = "UNKNOWN"
    for level_name, range_cfg in levels_config.items():
        min_val = range_cfg.get("min", 0)
        max_val = range_cfg.get("max", 100)
        if min_val <= overall_score <= max_val:
            risk_level = level_name.upper()
            break

    # Category scoring: min(sum(weights_for_category), 100)
    category_scores = {}
    for cat, weights in category_weights.items():
        category_scores[cat] = min(sum(weights), 100)
        print(f"[DIAGNOSTIC] Category '{cat}' weights: {weights} | Score: {category_scores[cat]}", flush=True)

    print(f"[DIAGNOSTIC] Final Risk Score: {overall_score}", flush=True)
    print(f"[DIAGNOSTIC] Final Risk Level: {risk_level}", flush=True)
    print(f"[DIAGNOSTIC] === Risk Engine Evaluation Ended ===\n", flush=True)

    return {
        "overall_risk": {
            "score": overall_score,
            "level": risk_level,
        },
        "category_scores": category_scores,
        "risk_signals": active_signals,
        "insufficient_evidence": insufficient_evidence,
    }


def persist_risk_analysis(
    db: Session,
    investigation_id: uuid.UUID,
    analysis_results: Dict[str, Any],
) -> List[RiskSignal]:
    """
    Clears old risk signals for the given investigation, saves new ones, and commits them.
    """
    try:
        # Clear old signals
        db.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).delete()

        created_signals = []
        for sig in analysis_results.get("risk_signals", []):
            db_sig = RiskSignal(
                investigation_id=investigation_id,
                category=sig["category"],
                code=sig["code"],
                severity=sig["severity"],
                description=sig["description"],
                risk_weight=sig["risk_weight"],
                confidence=sig["confidence"],
                evidence_ids=json.dumps(sig["evidence_ids"]),
            )
            db.add(db_sig)
            created_signals.append(db_sig)

        db.commit()
        for db_sig in created_signals:
            db.refresh(db_sig)
    except Exception:
        db.rollback()
        raise

    return created_signals

