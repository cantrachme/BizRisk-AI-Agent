import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Investigation
from app.schemas.investigation import InvestigationCreate

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCreate,
    db: Session = Depends(get_db),
) -> dict:
    if not any(
        [
            payload.business_name,
            payload.gstin,
            payload.cin,
            payload.website,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one business identifier.",
        )

    investigation = Investigation(
        input_data=payload.model_dump_json(),
    )

    db.add(investigation)
    db.commit()
    db.refresh(investigation)

    return {
        "id": str(investigation.id),
        "status": investigation.status,
    }


@router.get("/{investigation_id}")
def get_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    investigation = db.get(Investigation, investigation_id)

    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    return {
        "id": str(investigation.id),
        "status": investigation.status,
        "input": json.loads(investigation.input_data),
        "current_node": investigation.current_node,
        "retry_count": investigation.retry_count,
        "risk_score": investigation.risk_score,
        "risk_level": investigation.risk_level,
        "resolved_entity_id": str(investigation.resolved_entity_id) if investigation.resolved_entity_id else None,
        "entity_confidence": investigation.entity_confidence,
        "completed_at": investigation.completed_at,
        "created_at": investigation.created_at,
        "updated_at": investigation.updated_at,
    }


@router.get("/{investigation_id}/evidence")
def get_investigation_evidence(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list:
    from datetime import timezone
    from app.services.evidence import get_evidences_for_investigation

    investigation = db.get(Investigation, investigation_id)

    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    def format_dt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    evidences = get_evidences_for_investigation(db, investigation_id)
    return [
        {
            "id": str(ev.id),
            "investigation_id": str(ev.investigation_id),
            "research_result_id": ev.research_result_id,
            "task_id": ev.task_id,
            "field_name": ev.field_name,
            "field_value": ev.field_value,
            "source_name": ev.source_name,
            "source_url": ev.source_url,
            "retrieved_timestamp": format_dt(ev.retrieved_timestamp),
            "confidence": ev.confidence,
            "created_timestamp": format_dt(ev.created_timestamp),
        }
        for ev in evidences
    ]


@router.get("/{investigation_id}/risk")
def get_investigation_risk(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    from app.services.risk_analysis import analyze_investigation

    investigation = db.get(Investigation, investigation_id)

    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    try:
        analysis = analyze_investigation(db, investigation_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return {
        "overall_risk": analysis["overall_risk"],
        "category_scores": analysis["category_scores"],
        "risk_signals": [
            {
                "category": sig["category"],
                "code": sig["code"],
                "severity": sig["severity"],
                "description": sig["description"],
                "evidence_ids": sig["evidence_ids"],
                "confidence": sig["confidence"],
                "risk_weight": sig["risk_weight"],
            }
            for sig in analysis["risk_signals"]
        ],
    }


@router.get("/{investigation_id}/report")
def get_investigation_report(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    from app.models.report import Report
    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation_id)
        .order_by(Report.version.desc())
        .first()
    )
    if latest_report:
        return json.loads(latest_report.report_json)

    from app.services.report import generate_investigation_report
    try:
        report = generate_investigation_report(db, investigation_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return report


@router.get("/{investigation_id}/reports")
def get_investigation_reports(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list:
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    from app.models.report import Report
    reports = (
        db.query(Report)
        .filter(Report.investigation_id == investigation_id)
        .order_by(Report.version.asc())
        .all()
    )

    return [
        {
            "id": str(rep.id),
            "investigation_id": str(rep.investigation_id),
            "version": rep.version,
            "report": json.loads(rep.report_json),
            "qa_status": rep.qa_status,
            "created_at": rep.created_at.isoformat() if rep.created_at else None,
        }
        for rep in reports
    ]


@router.get("/{investigation_id}/qa")
def get_investigation_qa(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    from app.services.qa import validate_report

    try:
        qa_result = validate_report(db, investigation_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return qa_result


@router.get("/{investigation_id}/events")
def get_investigation_events(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list:
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    from app.models.investigation_event import InvestigationEvent
    events = (
        db.query(InvestigationEvent)
        .filter(InvestigationEvent.investigation_id == investigation_id)
        .order_by(InvestigationEvent.created_at.asc())
        .all()
    )

    return [
        {
            "id": str(evt.id),
            "investigation_id": str(evt.investigation_id),
            "event_type": evt.event_type,
            "node": evt.node,
            "status": evt.status,
            "metadata": json.loads(evt.metadata_json),
            "created_at": evt.created_at.isoformat() if evt.created_at else None,
        }
        for evt in events
    ]


@router.get("/{investigation_id}/human-intervention")
def get_human_intervention_status(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    from app.models.research_task import ResearchTask as ResearchTaskModel
    pending_intervention = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == investigation_id,
            ResearchTaskModel.status == "HUMAN_INTERVENTION_REQUIRED",
        )
        .all()
    )

    return {
        "investigation_id": str(investigation_id),
        "status": investigation.status,
        "pending_tasks": [
            {
                "id": str(t.id),
                "task_id": t.task_id,
                "task_type": t.task_type,
                "target": t.target,
                "objective": t.objective,
                "status": t.status,
                "intervention_type": t.intervention_type,
                "intervention_reason": t.intervention_reason,
            }
            for t in pending_intervention
        ]
    }


@router.post("/{investigation_id}/resume")
def resume_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    investigation = db.get(Investigation, investigation_id)
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    from app.models.research_task import ResearchTask as ResearchTaskModel
    from app.services.audit import record_event

    # 1. Fetch blocked tasks and set status to PENDING, clear intervention reason/type
    blocked_tasks = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == investigation_id,
            ResearchTaskModel.status == "HUMAN_INTERVENTION_REQUIRED",
        )
        .all()
    )

    for t in blocked_tasks:
        t.status = "PENDING"
        t.intervention_type = None
        t.intervention_reason = None
        t.started_at = None
        t.completed_at = None

    # 2. Update investigation status to PENDING_RESEARCH
    investigation.status = "PENDING_RESEARCH"
    db.commit()

    # 3. Log INVESTIGATION_RESUMED event
    record_event(
        db,
        investigation_id,
        "INVESTIGATION_RESUMED",
        "browser",
        "STARTED",
        {"resumed_tasks_count": len(blocked_tasks)}
    )

    # 4. Reconstruct graph state and trigger execution
    from app.graph.workflow import app as graph_app
    from app.services.research_task import get_research_tasks_for_investigation
    from app.services.evidence import get_evidences_for_investigation
    from app.graph.state import ResearchTask as GraphTask, ResearchResult as GraphResult

    tasks_db = get_research_tasks_for_investigation(db, investigation_id)
    evidences_db = get_evidences_for_investigation(db, investigation_id)

    pending_tasks = []
    completed_tasks = []
    failed_tasks = []

    def get_fields_for_task_type(task_type: str) -> list:
        if task_type == "ENTITY_DISCOVERY":
            return ["candidate_entities"]
        elif task_type == "GST_VERIFICATION":
            return ["legal_name", "gst_status", "registered_address", "business_activity"]
        elif task_type == "MCA_VERIFICATION":
            return ["legal_name", "company_status", "incorporation_date", "registered_address"]
        elif task_type == "WEBSITE_VERIFICATION":
            return ["website_status", "contact_address", "established_year"]
        else:
            return ["page_text"]

    def get_preferred_sources_for_task_type(task_type: str) -> list:
        if task_type == "GST_VERIFICATION":
            return ["gst.gov.in"]
        elif task_type == "MCA_VERIFICATION":
            return ["mca.gov.in"]
        elif task_type == "WEBSITE_VERIFICATION":
            return ["company_website"]
        else:
            return ["generic_web"]

    def get_fallback_sources_for_task_type(task_type: str) -> list:
        if task_type in {"GST_VERIFICATION", "MCA_VERIFICATION"}:
            return ["third_party"]
        elif task_type == "WEBSITE_VERIFICATION":
            return ["generic_web"]
        else:
            return []

    def get_priority_for_task_type(task_type: str) -> int:
        if task_type == "WEBSITE_VERIFICATION":
            return 2
        return 1

    for t in tasks_db:
        gt = GraphTask(
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status=t.status,
            priority=get_priority_for_task_type(t.task_type),
            required_fields=get_fields_for_task_type(t.task_type),
            preferred_sources=get_preferred_sources_for_task_type(t.task_type),
            fallback_sources=get_fallback_sources_for_task_type(t.task_type),
        )
        if t.status == "COMPLETED":
            completed_tasks.append(gt)
        elif t.status == "FAILED":
            failed_tasks.append(gt)
        else:
            pending_tasks.append(gt)

    results = []
    for ev in evidences_db:
        results.append(
            GraphResult(
                result_id=ev.research_result_id or f"RESULT-{ev.task_id}-001",
                task_id=ev.task_id,
                field_name=ev.field_name,
                field_value=ev.field_value,
                source_name=ev.source_name,
                source_url=ev.source_url,
                retrieved_at=ev.retrieved_timestamp.isoformat() if ev.retrieved_timestamp else "",
                confidence=ev.confidence,
            )
        )

    raw_input = json.loads(investigation.input_data)

    state = {
        "investigation_id": str(investigation_id),
        "raw_input": raw_input,
        "normalized_input": {},
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "results": results,
        "planner_loop_count": investigation.retry_count,
        "status": "PENDING_RESEARCH",
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    }

    # Execute graph
    graph_app.invoke(state)

    # Return updated investigation status
    db.refresh(investigation)
    return {
        "id": str(investigation.id),
        "status": investigation.status,
    }
