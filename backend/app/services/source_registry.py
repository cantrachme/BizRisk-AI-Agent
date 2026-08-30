import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from app.models.source_registry import SourceRegistry

def list_sources(db: Session, enabled_only: bool = False) -> List[SourceRegistry]:
    populate_default_sources(db)
    query = db.query(SourceRegistry)
    if enabled_only:
        query = query.filter(SourceRegistry.enabled == True)
    return query.order_by(SourceRegistry.priority.asc(), SourceRegistry.name.asc()).all()

def get_source(db: Session, source_id: uuid.UUID) -> Optional[SourceRegistry]:
    return db.get(SourceRegistry, source_id)

def get_source_by_name(db: Session, name: str) -> Optional[SourceRegistry]:
    populate_default_sources(db)
    return db.query(SourceRegistry).filter(SourceRegistry.name == name).first()

def create_source(
    db: Session,
    name: str,
    type: str,
    domain: Optional[str] = None,
    enabled: bool = True,
    priority: int = 1,
    config: Optional[Dict[str, Any]] = None
) -> SourceRegistry:
    existing = db.query(SourceRegistry).filter(
        SourceRegistry.name == name,
        SourceRegistry.type == type
    ).first()
    if existing:
        return existing
        
    config_str = json.dumps(config) if config is not None else None
    source = SourceRegistry(
        name=name,
        type=type,
        domain=domain,
        enabled=enabled,
        priority=priority,
        config_json=config_str
    )
    db.add(source)
    try:
        db.commit()
        db.refresh(source)
    except Exception:
        db.rollback()
        raise
    return source

def update_source(db: Session, source_id: uuid.UUID, **kwargs) -> Optional[SourceRegistry]:
    source = db.get(SourceRegistry, source_id)
    if not source:
        return None
    for k, v in kwargs.items():
        if k == "config" and isinstance(v, dict):
            source.config_json = json.dumps(v)
        elif hasattr(source, k):
            setattr(source, k, v)
    try:
        db.commit()
        db.refresh(source)
    except Exception:
        db.rollback()
        raise
    return source


def enable_source(db: Session, source_id: uuid.UUID, enabled: bool = True) -> Optional[SourceRegistry]:
    return update_source(db, source_id, enabled=enabled)

CANONICAL_SOURCE_MAP = {
    "GST Portal": "gst.gov.in",
    "MCA Portal": "mca.gov.in",
    "EPFO Portal": "epfindia.gov.in",
    "Company Website": "company_website",
    "General Web": "generic_web",
    "Third-Party Source": "third_party",
}

def to_canonical_source(name: str) -> str:
    return CANONICAL_SOURCE_MAP.get(name, name)

def get_preferred_sources(db: Session, task_type: str) -> Tuple[List[str], List[str]]:
    populate_default_sources(db)
    sources = (
        db.query(SourceRegistry)
        .filter(SourceRegistry.type == task_type, SourceRegistry.enabled == True)
        .order_by(SourceRegistry.priority.asc(), SourceRegistry.name.asc())
        .all()
    )
    if not sources:
        if task_type == "ENTITY_DISCOVERY":
            return ["generic_web"], []
        elif task_type == "GST_VERIFICATION":
            return ["gst.gov.in"], ["third_party"]
        elif task_type == "MCA_VERIFICATION":
            return ["mca.gov.in"], ["third_party"]
        elif task_type == "EPFO_VERIFICATION":
            return ["epfindia.gov.in"], ["third_party"]
        elif task_type == "WEBSITE_VERIFICATION":
            return ["company_website"], ["generic_web"]
        return [], []

    raw_names = [s.name for s in sources]
    canonical_names = []
    for name in raw_names:
        c_name = to_canonical_source(name)
        if c_name not in canonical_names:
            canonical_names.append(c_name)

    return [canonical_names[0]], canonical_names[1:]

def populate_default_sources(db: Session) -> None:
    try:
        db.query(SourceRegistry).first()
    except Exception:
        db.rollback()
        return


    defaults = [
        {
            "name": "gst.gov.in",
            "type": "GST_VERIFICATION",
            "domain": "https://www.gst.gov.in",
            "enabled": True,
            "priority": 1,
            "config": {"confidence": 0.95}
        },
        {
            "name": "third_party",
            "type": "GST_VERIFICATION",
            "domain": None,
            "enabled": True,
            "priority": 2,
            "config": {"confidence": 0.50}
        },
        {
            "name": "mca.gov.in",
            "type": "MCA_VERIFICATION",
            "domain": "https://www.mca.gov.in",
            "enabled": True,
            "priority": 1,
            "config": {"confidence": 0.95}
        },
        {
            "name": "third_party",
            "type": "MCA_VERIFICATION",
            "domain": None,
            "enabled": True,
            "priority": 2,
            "config": {"confidence": 0.50}
        },
        {
            "name": "epfindia.gov.in",
            "type": "EPFO_VERIFICATION",
            "domain": "https://www.epfindia.gov.in",
            "enabled": True,
            "priority": 1,
            "config": {"confidence": 0.90}
        },
        {
            "name": "third_party",
            "type": "EPFO_VERIFICATION",
            "domain": None,
            "enabled": True,
            "priority": 2,
            "config": {"confidence": 0.50}
        },
        {
            "name": "company_website",
            "type": "WEBSITE_VERIFICATION",
            "domain": None,
            "enabled": True,
            "priority": 1,
            "config": {"confidence": 0.85}
        },
        {
            "name": "generic_web",
            "type": "WEBSITE_VERIFICATION",
            "domain": None,
            "enabled": True,
            "priority": 2,
            "config": {"confidence": 0.60}
        },
        {
            "name": "generic_web",
            "type": "ENTITY_DISCOVERY",
            "domain": None,
            "enabled": True,
            "priority": 1,
            "config": {"confidence": 0.60}
        }
    ]

    for item in defaults:
        existing = db.query(SourceRegistry).filter(
            SourceRegistry.name == item["name"],
            SourceRegistry.type == item["type"]
        ).first()
        if not existing:
            source = SourceRegistry(
                name=item["name"],
                type=item["type"],
                domain=item["domain"],
                enabled=item["enabled"],
                priority=item["priority"],
                config_json=json.dumps(item["config"])
            )
            db.add(source)
    try:
        db.commit()
    except Exception:
        db.rollback()
