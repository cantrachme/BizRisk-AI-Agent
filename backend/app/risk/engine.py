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
            # Handle both SQLAlchemy objects and ResearchResult/dict objects
            source_name = getattr(ev, "source_name", None)
            if source_name is None and isinstance(ev, dict):
                source_name = ev.get("source_name")

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
        if not (0.0 <= float(ev.confidence) <= 1.0):
            continue
        seen_ids.add(evidence_id)
        validated_evs.append(ev)

    # Evaluate deterministic rules only against validated evidence.
    triggered_rules = run_all_rules(validated_evs)

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

    return {
        "overall_risk": {
            "score": overall_score,
            "level": risk_level,
        },
        "category_scores": category_scores,
        "risk_signals": active_signals,
    }


def persist_risk_analysis(
    db: Session,
    investigation_id: uuid.UUID,
    analysis_results: Dict[str, Any],
) -> List[RiskSignal]:
    """
    Clears old risk signals for the given investigation, saves new ones, and commits them.
    """
    # Clear old signals
    db.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).delete()
    db.commit()

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

    return created_signals
