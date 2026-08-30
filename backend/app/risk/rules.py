import re
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dateutil import parser as date_parser

class NormalizedEvidence:
    def __init__(
        self,
        id: str,
        task_id: str,
        field_name: str,
        field_value: Any,
        source_name: str,
        source_url: Optional[str],
        retrieved_at: datetime,
        confidence: float,
    ):
        self.id = id
        self.task_id = task_id
        self.field_name = field_name
        self.field_value = field_value
        self.source_name = source_name
        self.source_url = source_url
        self.retrieved_at = retrieved_at
        self.confidence = confidence


def normalize_evidence(res: Any) -> NormalizedEvidence:
    """
    Normalizes both ResearchResult (Pydantic model) and Evidence (SQLAlchemy model)
    into a standardized NormalizedEvidence container.
    """
    # Evidence SQLAlchemy model
    if hasattr(res, "research_result_id"):
        retrieved_dt = res.retrieved_timestamp
        if retrieved_dt is None:
            retrieved_dt = datetime.now(timezone.utc)
        elif retrieved_dt.tzinfo is None:
            retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
        val = res.field_value
        try:
            # Decode JSON list or dict if stored stringified
            val_loaded = json.loads(val)
            if isinstance(val_loaded, (list, dict)):
                val = val_loaded
        except (ValueError, TypeError):
            pass

        confidence = res.confidence
        # Force confidence to 0.0 for failed/not found evidence values
        if val in ["NOT_FOUND", "UNAVAILABLE"]:
            confidence = 0.0
        elif isinstance(val, dict) and str(val.get("text")).strip().upper() == "NOT_FOUND":
            confidence = 0.0

        return NormalizedEvidence(
            id=res.research_result_id,
            task_id=res.task_id,
            field_name=res.field_name,
            field_value=val,
            source_name=res.source_name,
            source_url=res.source_url,
            retrieved_at=retrieved_dt,
            confidence=confidence,
        )
    # ResearchResult Pydantic model
    else:
        try:
            retrieved_dt = datetime.fromisoformat(res.retrieved_at)
        except ValueError:
            retrieved_dt = datetime.now(timezone.utc)
        if retrieved_dt.tzinfo is None:
            retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)

        val = res.field_value
        confidence = res.confidence
        # Force confidence to 0.0 for failed/not found evidence values
        if val in ["NOT_FOUND", "UNAVAILABLE"]:
            confidence = 0.0
        elif isinstance(val, dict) and str(val.get("text")).strip().upper() == "NOT_FOUND":
            confidence = 0.0

        return NormalizedEvidence(
            id=res.result_id,
            task_id=res.task_id,
            field_name=res.field_name,
            field_value=val,
            source_name=res.source_name,
            source_url=res.source_url,
            retrieved_at=retrieved_dt,
            confidence=confidence,
        )


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    for suffix in ["pvtltd", "privatelimited", "llp", "ltd", "limited", "co", "company"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def is_full_address(addr: Any) -> bool:
    if not addr:
        return False
    if isinstance(addr, dict):
        addr_str = str(addr.get("text") or addr.get("title") or "").lower()
    else:
        addr_str = str(addr).lower()
        
    if addr_str.upper() == "NOT_FOUND":
        return False
        
    words = [w for w in re.findall(r"\b\w+\b", addr_str)]
    if len(words) < 2:
        return False
    street_keywords = {
        "street", "road", "st", "rd", "plot", "sector", "phase", "building", "floor", 
        "nagar", "puram", "lane", "hno", "flat", "office", "address", "addr", "block", "place", 
        "colony", "landmark", "near", "behind", "opposite", "bazar", "market", "extension", 
        "ext", "chowk", "square", "avenue", "drive", "highway"
    }
    if not any(kw in words for kw in street_keywords) and len(words) < 5:
        return False
    return True


def normalize_address(addr: Any) -> str:
    if not addr:
        return ""
    if isinstance(addr, dict):
        addr_str = str(addr.get("text") or addr.get("title") or "").lower()
    else:
        addr_str = str(addr).lower()
    return re.sub(r"[^a-z0-9]", "", addr_str)


def normalize_activity(act: str) -> str:
    if not act:
        return ""
    return re.sub(r"[^a-z0-9]", "", act.lower())


def evaluate_gst_inactive(evidences: List[NormalizedEvidence]) -> Optional[Dict[str, Any]]:
    for ev in evidences:
        if ev.field_name == "gst_status":
            val = str(ev.field_value).strip().lower()
            if val in ["inactive", "cancelled", "suspended", "no", "invalid", "false"]:
                return {
                    "triggered": True,
                    "evidence_ids": [ev.id],
                    "confidence": ev.confidence,
                    "description": f"GST status is inactive: '{ev.field_value}' from source '{ev.source_name}'.",
                }
    return None


def evaluate_legal_name_conflict(evidences: List[NormalizedEvidence]) -> Optional[Dict[str, Any]]:
    name_evidences = [ev for ev in evidences if ev.field_name in ["legal_name", "business_name"]]
    if len(name_evidences) < 2:
        return None

    # Compare pairwise to find any conflict
    for i in range(len(name_evidences)):
        for j in range(i + 1, len(name_evidences)):
            ev1 = name_evidences[i]
            ev2 = name_evidences[j]
            norm1 = normalize_name(str(ev1.field_value))
            norm2 = normalize_name(str(ev2.field_value))
            if norm1 and norm2 and norm1 != norm2:
                # Average confidence
                avg_conf = (ev1.confidence + ev2.confidence) / 2.0
                return {
                    "triggered": True,
                    "evidence_ids": [ev1.id, ev2.id],
                    "confidence": avg_conf,
                    "description": f"Legal name mismatch: '{ev1.field_value}' ({ev1.source_name}) vs '{ev2.field_value}' ({ev2.source_name}).",
                }
    return None


def evaluate_address_major_mismatch(evidences: List[NormalizedEvidence]) -> Optional[Dict[str, Any]]:
    # Group evidences by field type to avoid cross-comparing registered vs contact addresses
    groups = {
        "registered": [ev for ev in evidences if ev.field_name in ["registered_address", "address"]],
        "contact": [ev for ev in evidences if ev.field_name in ["contact_address"]],
    }

    for group_name, group_evs in groups.items():
        if len(group_evs) < 2:
            continue
        for i in range(len(group_evs)):
            for j in range(i + 1, len(group_evs)):
                ev1 = group_evs[i]
                ev2 = group_evs[j]
                
                # Check if both are full addresses
                if not is_full_address(ev1.field_value) or not is_full_address(ev2.field_value):
                    continue
                
                norm1 = normalize_address(ev1.field_value)
                norm2 = normalize_address(ev2.field_value)
                if norm1 and norm2 and norm1 != norm2:
                    avg_conf = (ev1.confidence + ev2.confidence) / 2.0
                    return {
                        "triggered": True,
                        "evidence_ids": [ev1.id, ev2.id],
                        "confidence": avg_conf,
                        "description": f"Address mismatch ({group_name}): '{ev1.field_value}' ({ev1.source_name}) vs '{ev2.field_value}' ({ev2.source_name}).",
                    }
    return None


def evaluate_business_activity_mismatch(evidences: List[NormalizedEvidence]) -> Optional[Dict[str, Any]]:
    activity_evidences = [ev for ev in evidences if ev.field_name == "business_activity"]
    if len(activity_evidences) < 2:
        return None

    for i in range(len(activity_evidences)):
        for j in range(i + 1, len(activity_evidences)):
            ev1 = activity_evidences[i]
            ev2 = activity_evidences[j]
            norm1 = normalize_activity(str(ev1.field_value))
            norm2 = normalize_activity(str(ev2.field_value))
            if norm1 and norm2 and norm1 != norm2:
                avg_conf = (ev1.confidence + ev2.confidence) / 2.0
                return {
                    "triggered": True,
                    "evidence_ids": [ev1.id, ev2.id],
                    "confidence": avg_conf,
                    "description": f"Business activity mismatch: '{ev1.field_value}' ({ev1.source_name}) vs '{ev2.field_value}' ({ev2.source_name}).",
                }
    return None


def evaluate_very_recent_registration(evidences: List[NormalizedEvidence]) -> Optional[Dict[str, Any]]:
    for ev in evidences:
        if ev.field_name in ["registration_date", "incorporation_date"]:
            try:
                reg_date = date_parser.parse(str(ev.field_value))
                if reg_date.tzinfo is not None:
                    reg_date = reg_date.replace(tzinfo=None)
                retrieved_naive = ev.retrieved_at.replace(tzinfo=None)
                age_days = (retrieved_naive - reg_date).days
                if age_days < 365:
                    return {
                        "triggered": True,
                        "evidence_ids": [ev.id],
                        "confidence": ev.confidence,
                        "description": f"Very recent business registration: '{ev.field_value}' (less than 1 year old).",
                    }
            except Exception:
                pass
    return None


def run_all_rules(evidences: List[NormalizedEvidence]) -> Dict[str, Dict[str, Any]]:
    """
    Evaluates all rules on the normalized evidence list.
    Returns a dictionary of rule_code -> rule_result (containing triggered, evidence_ids, confidence, description).
    """
    results = {}

    gst_res = evaluate_gst_inactive(evidences)
    if gst_res:
        results["GST_INACTIVE"] = gst_res

    name_res = evaluate_legal_name_conflict(evidences)
    if name_res:
        results["LEGAL_NAME_CONFLICT"] = name_res

    addr_res = evaluate_address_major_mismatch(evidences)
    if addr_res:
        results["ADDRESS_MAJOR_MISMATCH"] = addr_res

    act_res = evaluate_business_activity_mismatch(evidences)
    if act_res:
        results["BUSINESS_ACTIVITY_MISMATCH"] = act_res

    rec_res = evaluate_very_recent_registration(evidences)
    if rec_res:
        results["VERY_RECENT_REGISTRATION"] = rec_res

    return results
