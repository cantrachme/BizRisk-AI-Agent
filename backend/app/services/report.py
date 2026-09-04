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


def _evidence_is_verified(ev) -> bool:
    """A single evidence record counts as 'verified' for the source-level status
    rollup only when its own ``verification_status`` says so. Raw ``confidence``
    is a legacy fallback, used only when ``verification_status`` was never
    recorded (``None`` / empty) -- an explicit ``UNVERIFIED`` never qualifies.
    """
    if str(getattr(ev, "field_value", "")).strip().upper() in {"NOT_FOUND", "UNAVAILABLE", "NONE", "BLOCKED"}:
        return False
    raw_status = getattr(ev, "verification_status", None)
    status = str(raw_status).strip().upper() if raw_status is not None else ""
    if status:
        return status == "VERIFIED"
    try:
        return float(getattr(ev, "confidence", 0.0) or 0.0) >= 0.70
    except (TypeError, ValueError):
        return False


def _classify_source_status(source_evidences: list) -> tuple[str, str]:
    if not source_evidences:
        return "UNAVAILABLE", "No research evidence retrieved from this source."

    # Best valid outcome first: if this source contributed any genuinely verified
    # evidence, that is its status -- a stray blocked / captcha marker in another
    # field's value can never demote it.
    if any(_evidence_is_verified(ev) for ev in source_evidences):
        return "VERIFIED", "Evidence obtained and verified against target entity."

    has_captcha = any("CAPTCHA" in str(ev.field_value).upper() or "CAPTCHA" in str(ev.field_name).upper() for ev in source_evidences)
    if has_captcha:
        return "CAPTCHA_REQUIRED", "Source requires human verification or CAPTCHA challenge."

    has_blocked = any(str(ev.field_value).strip().upper() in {"BLOCKED", "SOURCE_UNAVAILABLE", "TIMEOUT"} for ev in source_evidences)
    if has_blocked:
        return "BLOCKED", "Source access was blocked or timed out."

    has_not_found = any(str(ev.field_value).strip().upper() in {"NOT_FOUND", "NONE"} for ev in source_evidences)
    if has_not_found:
        return "NOT_FOUND", "Target entity record not found in this registry."

    return "UNAVAILABLE", "Source information unavailable or unverified."


# Rank of a single browser attempt outcome, best (highest) to worst. Used to roll
# every stored attempt for one source up to a single "final source status" that
# always reflects the best outcome -- a later blocked/failed attempt can never
# demote an earlier SUCCESS, and a SUCCESS on a fallback URL is never demoted
# because the primary URL failed.
_ATTEMPT_STATUS_RANK = {
    "SUCCESS": 6,
    "NO_DATA": 5, "NO_RESULTS": 5, "NOT_FOUND": 5,
    "REJECTED": 4, "ENTITY_MISMATCH": 4, "UNRELATED": 4,
    "IRRELEVANT_CONTENT": 3, "IRRELEVANT_SECTOR": 3, "IRRELEVANT": 3,
    "CAPTCHA_REQUIRED": 2, "BLOCKED": 2, "BLOCKED_OR_ERROR": 2,
    "ERROR": 1, "EMPTY_RESPONSE": 1,
}


def derive_browser_source_statuses(sessions) -> dict[str, dict[str, Any]]:
    """
    Roll every stored browser attempt up to one final status per source
    (``BrowserSession.domain``): the best outcome across all of that source's
    attempts. Individual attempt rows are still kept for diagnostics -- this is
    only a derived rollup and never rewrites them.
    """
    by_domain: dict[str, list[str]] = {}
    for s in sessions:
        domain = (getattr(s, "domain", None)
                  if not isinstance(s, dict) else s.get("domain")) or ""
        status = (getattr(s, "status", None)
                  if not isinstance(s, dict) else s.get("status")) or ""
        domain = str(domain).strip()
        status = str(status).strip().upper()
        if not domain:
            continue
        by_domain.setdefault(domain, []).append(status)

    out: dict[str, dict[str, Any]] = {}
    for domain, statuses in by_domain.items():
        best = max(statuses, key=lambda st: _ATTEMPT_STATUS_RANK.get(st, 0))
        out[domain] = {
            "status": best,
            "attempts": len(statuses),
            "attempt_statuses": statuses,
        }
    return out


def build_verification_summary(evidences: list) -> dict[str, dict[str, str]]:
    by_source_category = {
        "gst": [],
        "mca": [],
        "epfo": [],
        "official_website": [],
        "third_party": [],
        "general_web": [],
    }

    from app.research.source_registry import SourceType, source_registry

    for ev in evidences:
        # candidate_entities rows are discovery *leads* (produced by the
        # discovery agent and by general-web scans), never a source's
        # verification of a fact about the target. Counting them would inflate a
        # source and, via the legacy confidence fallback, could mark it VERIFIED
        # without any genuinely verified evidence. Excluded here exactly as the
        # Risk Engine already excludes them from scoring.
        if getattr(ev, "field_name", None) == "candidate_entities":
            continue
        src = (ev.source_name or "").lower()
        # A source registered in the source registry as a THIRD_PARTY_REGISTRY
        # is categorised as third-party regardless of its display name, so newly
        # registered directory sources need no keyword here.
        _meta = source_registry.get_source(ev.source_name or "")
        _is_registered_tp = bool(
            _meta and getattr(_meta, "source_type", None) == SourceType.THIRD_PARTY_REGISTRY
        )
        if "gst" in src:
            by_source_category["gst"].append(ev)
        elif "mca" in src:
            by_source_category["mca"].append(ev)
        elif "epf" in src or "epfo" in src:
            by_source_category["epfo"].append(ev)
        # Third-party registries must be matched BEFORE the generic
        # "company"/"website" check, otherwise e.g. "QuickCompany" (contains
        # "company") is misfiled as the official website.
        elif _is_registered_tp or any(k in src for k in [
            "third_party", "third-party", "third party", "zauba", "tofler",
            "quickcompany", "quick company", "instafinancial", "registry", "directory",
        ]):
            by_source_category["third_party"].append(ev)
        elif "website" in src or "company" in src:
            by_source_category["official_website"].append(ev)
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


_SOURCE_CATEGORY_LABELS = {
    "gst": "GST Portal",
    "mca": "MCA Portal",
    "epfo": "EPFO Portal",
    "official_website": "Company Website",
    "third_party": "Third-Party Registry",
    "general_web": "General Web",
}

_LIMITATION_STATUSES = {"UNAVAILABLE", "BLOCKED", "CAPTCHA_REQUIRED", "NOT_FOUND"}


def _attempt_domain_to_category(domain: str | None) -> str:
    d = (domain or "").lower()
    if "gst" in d:
        return "gst"
    if "mca" in d:
        return "mca"
    if "epf" in d:
        return "epfo"
    if "company_website" in d or "website" in d:
        return "official_website"
    if any(k in d for k in ("third_party", "zauba", "tofler", "quick", "insta")):
        return "third_party"
    return "general_web"


_SOURCE_AUTHORITY_RANK = [
    ("gst", ("gst",)),
    ("mca", ("mca",)),
    ("epfo", ("epf", "epfo")),
    ("official_website", ("company website", "official website")),
    ("third_party", ("zauba", "tofler", "quickcompany", "quick company", "instafinancials", "third-party", "third party", "registry")),
    ("general_web", ("general web", "web")),
]


def _primary_source(evidences: list) -> str | None:
    """
    Global "source of record", derived ONLY from finally-persisted, usable
    evidence: the highest-authority source that contributed at least one
    verified/usable field, breaking ties by confidence then name. Never derived
    from browser attempt history, so it cannot contradict field-level evidence.
    """
    best = None  # (authority_rank, -confidence, source_name)
    for ev in evidences:
        val = str(ev.field_value).strip().upper()
        conf = ev.confidence or 0.0
        if conf < 0.50 or val in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE", "BLOCKED", "TIMEOUT", "NONE", "ERROR"}:
            continue
        src = str(ev.source_name or "").strip()
        low = src.lower()
        rank = len(_SOURCE_AUTHORITY_RANK)
        for i, (_cat, needles) in enumerate(_SOURCE_AUTHORITY_RANK):
            if any(n in low for n in needles):
                rank = i
                break
        key = (rank, -float(conf), low)
        if best is None or key < best[0]:
            best = (key, src)
    return best[1] if best else None


def _build_source_limitations(db, investigation_id, verification_summary: dict) -> list[dict[str, Any]]:
    """
    Emit at most one limitation per *logical source*, derived from the final
    per-source status (verification_summary, itself built from persisted
    evidence). A limitation is reported only when the source produced no usable
    evidence AND at least one meaningful attempt was made for it. Earlier failed
    attempts for a source that ultimately succeeded remain only in the
    BrowserSession attempt diagnostics.
    """
    from app.models.browser_session import BrowserSession

    attempted_categories: set[str] = set()
    try:
        rows = (
            db.query(BrowserSession)
            .filter(BrowserSession.investigation_id == investigation_id)
            .all()
        )
        for bs in rows:
            attempted_categories.add(_attempt_domain_to_category(bs.domain))
    except Exception:
        attempted_categories = set()

    limitations: list[dict[str, Any]] = []
    for cat, data in verification_summary.items():
        status = (data or {}).get("status")
        if status not in _LIMITATION_STATUSES:
            continue
        if cat not in attempted_categories:
            continue
        label = _SOURCE_CATEGORY_LABELS.get(cat, cat)
        limitations.append({
            "source": label,
            "field": "source_access",
            "status": status,
            "reason": (
                f"{label} was attempted but no usable target-entity evidence "
                f"could be obtained ({status})."
            ),
        })
    return limitations


def normalize_address_for_reconciliation(addr: str | None) -> str:
    if not addr:
        return ""
    import re
    text = addr.lower().strip()
    replacements = [
        (r"\b(flr|floor|fl)\b", "floor"),
        (r"\b(bldg|building)\b", "building"),
        (r"\b(rd|road)\b", "road"),
        (r"\b(st|street)\b", "street"),
        (r"\b(apt|apartment)\b", "apartment"),
        (r"\b(off|office)\b", "office"),
        (r"\b(pt|point)\b", "point"),
        (r"\b(pl|plot)\b", "plot"),
        (r"\b(no|num|number)\b", "no"),
        (r"\b(opp|opposite)\b", "opp"),
        (r"\b(nr|near)\b", "near"),
        (r"\b(dist|district)\b", "dist"),
        (r"\b(sec|sector)\b", "sector"),
        (r"\b(ph|phase)\b", "phase"),
        (r"\b(hno|house\s+no)\b", "hno"),
        (r"\bindia\b", ""),
        (r"\bind\b", ""),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", text)


def compare_semantic_fields(field_a: str, val_a: str, field_b: str, val_b: str) -> str:
    """
    Compares two values from given semantic fields.
    If fields belong to different semantic categories, returns 'NOT_COMPARABLE'.
    If values are missing, returns 'UNAVAILABLE'.
    If fields are compatible, normalizes and returns 'MATCH' or 'CONFLICT'.
    """
    if field_a != field_b:
        return "NOT_COMPARABLE"
    if not val_a or not val_b:
        return "UNAVAILABLE"
    if field_a in {"registered_address", "establishment_address", "contact_address", "principal_business_address", "address"}:
        norm_a = normalize_address_for_reconciliation(val_a)
        norm_b = normalize_address_for_reconciliation(val_b)
        return "MATCH" if (norm_a and norm_b and norm_a == norm_b) else "CONFLICT"
    return "MATCH" if val_a.strip().lower() == val_b.strip().lower() else "CONFLICT"


def build_cross_source_consistency(evidences: list, entity: dict) -> list[dict[str, Any]]:
    fields_to_reconcile = [
        ("legal_name", "Legal Entity Name"),
        ("gstin", "GSTIN Identifier"),
        ("cin", "CIN Identifier"),
        ("registered_address", "Registered Office Address"),
        ("establishment_address", "EPFO Establishment Address"),
        ("contact_address", "Official Contact Address"),
        ("principal_business_address", "Principal Place of Business"),
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
                if val.upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE", "BLOCKED", "ERROR", "UNKNOWN"}:
                    field_values[ev.source_name] = val

        # Also compare against user-supplied target value if present
        target_val = entity.get(field_key)
        if target_val and str(target_val).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE", "UNKNOWN"}:
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

        sources_list = [{"source": src, "value": val} for src, val in field_values.items()]

        # Address field normalization
        if field_key in {"registered_address", "establishment_address", "contact_address", "principal_business_address", "address"}:
            norm_map = {}
            for src, val in field_values.items():
                norm = normalize_address_for_reconciliation(val)
                norm_map[src] = norm
            unique_norms = set(norm_map.values())
            if len(unique_norms) == 1:
                status = "MATCH"
                analysis = f"All sources ({', '.join(field_values.keys())}) are fully consistent."
            else:
                status = "CONFLICT"
                analysis = f"Conflicting address data reported across sources: {'; '.join([f'{s}: {v}' for s, v in field_values.items()])}."

        else:
            unique_vals = list({v.upper(): v for v in field_values.values()}.values())
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

        # Collect source limitations and unverified info.
        #
        # Source status is derived ONLY from finally-persisted evidence
        # (verification_summary). Raw browser attempt rows are diagnostics and
        # never drive final source status: a source that failed once and later
        # succeeded is VERIFIED, not limited. Limitations are emitted at most
        # once per logical source, and only when that source produced no usable
        # evidence *and* was actually attempted.
        unverified_info = []
        reason_codes = list(analysis.get("reason_codes") or [])

        for ev in sorted_evidences:
            val = ev.field_value
            val_str = str(val).strip().upper()
            if (
                ev.confidence < 0.50
                and val_str not in {"NOT_FOUND", "NONE", "UNAVAILABLE", "SOURCE_UNAVAILABLE", "BLOCKED", "TIMEOUT"}
            ):
                unverified_info.append({
                    "field": ev.field_name,
                    "value": val,
                    "source": ev.source_name,
                })

        verification_summary = build_verification_summary(sorted_evidences)
        cross_source_consistency = build_cross_source_consistency(sorted_evidences, entity)

        source_limitations = _build_source_limitations(db, investigation_id, verification_summary)

        # Per-source FINAL attempt status = best outcome across every stored
        # browser attempt for that source (diagnostic only; does not feed risk,
        # assessment, or verification_summary).
        from app.models.browser_session import BrowserSession
        try:
            _bs_rows = (
                db.query(BrowserSession)
                .filter(BrowserSession.investigation_id == investigation_id)
                .all()
            )
        except Exception:
            _bs_rows = []
        source_attempt_status = derive_browser_source_statuses(_bs_rows)

        is_insufficient = analysis.get("insufficient_evidence", False) or analysis["overall_risk"]["score"] is None
        if is_insufficient:
            if (
                source_limitations
                or verification_summary.get("gst", {}).get("status") in {"UNAVAILABLE", "NOT_FOUND", "BLOCKED", "CAPTCHA_REQUIRED"}
                or verification_summary.get("mca", {}).get("status") in {"UNAVAILABLE", "NOT_FOUND", "BLOCKED", "CAPTCHA_REQUIRED"}
            ):
                if "AUTHORITATIVE_SOURCES_UNAVAILABLE" not in reason_codes:
                    reason_codes.append("AUTHORITATIVE_SOURCES_UNAVAILABLE")
            if not entity.get("gstin") and not entity.get("cin"):
                if "NO_VERIFIED_ENTITY_RECORD" not in reason_codes:
                    reason_codes.append("NO_VERIFIED_ENTITY_RECORD")
            if "INSUFFICIENT_EVIDENCE" not in reason_codes:
                reason_codes.append("INSUFFICIENT_EVIDENCE")

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
            "primary_source": _primary_source(sorted_evidences),
            "cross_source_consistency": cross_source_consistency,
            "source_limitations": source_limitations,
            "source_attempt_status": source_attempt_status,
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

        # 7b. Optional LLM narrative (additive, non-authoritative).
        #     The deterministic Risk Engine score/level above are the sole
        #     authority; they are handed to the LLM read-only and the narrative
        #     schema has no score field.
        report_dict["narrative"] = ""
        report_dict["cross_source_consistency_narrative"] = ""
        try:
            from app.core.llm import run_structured_sync
            from app.schemas.agent_outputs import ReportNarrative

            narrative_context = {
                "resolved_entity": {
                    k: entity.get(k)
                    for k in ("business_name", "name", "gstin", "cin", "website", "state")
                    if entity.get(k)
                },
                "entity_confidence": entity_confidence,
                "assessment_status": report_dict["assessment_status"],
                # read-only: the LLM must not recompute or override these
                "risk_score_readonly": analysis["overall_risk"]["score"],
                "risk_level_readonly": analysis["overall_risk"]["level"],
                "category_scores_readonly": analysis["category_scores"],
                "major_findings": [
                    {
                        "code": f.get("code"),
                        "severity": f.get("severity"),
                        "description": f.get("description"),
                    }
                    for f in major_findings
                ],
                "cross_source_consistency": cross_source_consistency,
                "reason_codes": reason_codes,
            }
            narrative_out = run_structured_sync(
                resolved_llm,
                f"{prompt}\n\nWrite a concise, evidence-grounded due-diligence narrative for the "
                f"following investigation. Do NOT output any risk score or risk level; they are "
                f"provided read-only for context only.\n\n{json.dumps(narrative_context, default=str)}",
                ReportNarrative,
                system_instruction=(
                    "You write neutral business due-diligence report narratives. Never state or "
                    "imply a company is fraudulent. Never output a numeric risk score or risk "
                    "level — those are computed deterministically elsewhere and given to you "
                    "read-only. Ground every statement in the supplied findings and evidence."
                ),
            )
            if narrative_out is not None:
                report_dict["narrative"] = narrative_out.narrative_summary or ""
                report_dict["cross_source_consistency_narrative"] = (
                    narrative_out.cross_source_consistency_summary or ""
                )
                if narrative_out.recommended_verification_focus:
                    report_dict["meta"]["recommended_verification_focus"] = list(
                        narrative_out.recommended_verification_focus
                    )
        except Exception:
            # Narrative is best-effort; never block report generation.
            report_dict["narrative"] = report_dict.get("narrative") or ""
            report_dict["cross_source_consistency_narrative"] = (
                report_dict.get("cross_source_consistency_narrative") or ""
            )

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
