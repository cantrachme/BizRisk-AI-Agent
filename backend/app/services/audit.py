import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.investigation_event import InvestigationEvent

logger = logging.getLogger("bizrisk.observability")
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def record_event(
    db: Optional[Session],
    investigation_id: uuid.UUID,
    event_type: str,
    node: str,
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Structured logging and database persistence of investigation pipeline lifecycle events.
    """
    log_payload = {
        "investigation_id": str(investigation_id),
        "event_type": event_type,
        "node": node,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    }
    # Log structured event to standard logger (stdout/stderr)
    logger.info(json.dumps(log_payload))

    # Persist structured event to DB if session is available
    if db is not None:
        try:
            event = InvestigationEvent(
                investigation_id=investigation_id,
                event_type=event_type,
                node=node,
                status=status,
                metadata_json=json.dumps(metadata or {}),
            )
            db.add(event)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to persist event to database for investigation {investigation_id}: {e}")
