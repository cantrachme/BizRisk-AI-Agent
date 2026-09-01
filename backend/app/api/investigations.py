import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status, BackgroundTasks
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Investigation
from app.schemas.investigation import InvestigationCreate
from app.api.auth import get_current_user_id, get_owned_investigation

router = APIRouter(prefix="/investigations", tags=["investigations"])


def run_investigation_workflow(investigation_id: uuid.UUID):
    from app.db.session import SessionLocal
    from app.graph.workflow import app as graph_app
    from app.services.investigation import recover_investigation_state, serialize_state
    from app.models.investigation import Investigation
    import logging
    import json

    logger = logging.getLogger("bizrisk.background")

    with SessionLocal() as db:
        inv = db.get(Investigation, investigation_id)
        if not inv:
            logger.error(f"Background task: Investigation {investigation_id} not found.")
            return
        if inv.status != "created":
            logger.warning(f"Background task: Investigation {investigation_id} is already in status {inv.status}. Skipping execution.")
            return

        try:
            inv.status = "PENDING"
            state = recover_investigation_state(db, investigation_id)
            state["status"] = "PENDING"
            inv.persistent_graph_state = serialize_state(state)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Background task: Failed to initialize graph state for {investigation_id}: {e}")
            return

    try:
        graph_app.invoke(state)
    except Exception as e:
        logger.error(f"Background task: Exception in workflow execution for {investigation_id}: {e}", exc_info=True)
        with SessionLocal() as db:
            inv = db.get(Investigation, investigation_id)
            if inv:
                inv.status = "FAILED"
                try:
                    db.commit()
                except Exception:
                    db.rollback()


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCreate,
    background_tasks: BackgroundTasks,
    current_user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if not any(
        [
            payload.business_name,
            payload.gstin,
            payload.cin,
            payload.epfo_code,
            payload.website,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one business identifier.",
        )

    investigation = Investigation(
        input_data=payload.model_dump_json(),
        user_id=current_user_id,
        raw_input=payload.model_dump_json(),
        status="created",
    )

    try:
        db.add(investigation)
        db.commit()
        db.refresh(investigation)
    except Exception:
        db.rollback()
        raise

    background_tasks.add_task(run_investigation_workflow, investigation.id)

    return {
        "id": str(investigation.id),
        "status": investigation.status,
    }


@router.get("/")
def list_investigations(
    status: str | None = None,
    current_user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list:
    from app.models.investigation import Investigation
    query = db.query(Investigation).filter(Investigation.user_id == current_user_id)
    if status:
        query = query.filter(Investigation.status == status)
    investigations = query.order_by(Investigation.created_at.desc()).all()
    return [
        {
            "id": str(inv.id),
            "status": inv.status,
            "current_node": inv.current_node,
            "input": json.loads(inv.input_data) if inv.input_data else {},
            "risk_score": inv.risk_score,
            "risk_level": inv.risk_level,
            "resolved_entity_id": str(inv.resolved_entity_id) if inv.resolved_entity_id else None,
            "entity_confidence": inv.entity_confidence,
            "completed_at": inv.completed_timestamp.isoformat() if inv.completed_timestamp else None,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
        }
        for inv in investigations
    ]


@router.get("/incomplete")
def list_incomplete_investigations(
    current_user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list:
    from app.models.investigation import Investigation
    incomplete = (
        db.query(Investigation)
        .filter(
            Investigation.user_id == current_user_id,
            Investigation.completed_timestamp.is_(None),
            Investigation.status.notin_(["COMPLETED", "FAILED"])
        )
        .all()
    )
    return [
        {
            "id": str(inv.id),
            "status": inv.status,
            "current_node": inv.current_node,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in incomplete
    ]


@router.get("/{investigation_id}")
def get_investigation(
    investigation: Investigation = Depends(get_owned_investigation),
) -> dict:
    sessions_db = investigation.browser_sessions
    browser_sessions_list = []
    for s in sessions_db:
        attempt_details = {}
        if s.failure_reason:
            try:
                attempt_details = json.loads(s.failure_reason)
            except Exception:
                attempt_details = {"raw_failure_reason": s.failure_reason}
        
        browser_sessions_list.append({
            "id": str(s.id),
            "task_id": s.task_id,
            "domain": s.domain,
            "status": s.status,
            "action_count": s.action_count,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            **attempt_details
        })

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
        "completed_at": investigation.completed_timestamp,
        "created_at": investigation.created_at,
        "updated_at": investigation.updated_at,
        "browser_sessions": browser_sessions_list,
    }


@router.get("/{investigation_id}/evidence")
def get_investigation_evidence(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> list:
    from datetime import timezone
    from app.services.evidence import get_evidences_for_investigation

    def format_dt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    evidences = get_evidences_for_investigation(db, investigation.id)
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
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.risk_analysis import analyze_investigation

    try:
        analysis = analyze_investigation(db, investigation.id)
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
        "insufficient_evidence": analysis.get("insufficient_evidence", False),
    }


@router.get("/{investigation_id}/report")
def get_investigation_report(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.report import Report
    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation.id)
        .order_by(Report.version.desc())
        .first()
    )
    if latest_report:
        return json.loads(latest_report.report_json)

    from app.services.report import generate_investigation_report
    try:
        report = generate_investigation_report(db, investigation.id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return report


@router.get("/{investigation_id}/reports")
def get_investigation_reports(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> list:
    from app.models.report import Report
    reports = (
        db.query(Report)
        .filter(Report.investigation_id == investigation.id)
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
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.qa import validate_report

    try:
        qa_result = validate_report(db, investigation.id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return qa_result


@router.get("/{investigation_id}/events")
def get_investigation_events(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> list:
    from app.models.investigation_event import InvestigationEvent
    events = (
        db.query(InvestigationEvent)
        .filter(InvestigationEvent.investigation_id == investigation.id)
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
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.research_task import ResearchTask as ResearchTaskModel
    pending_intervention = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == investigation.id,
            ResearchTaskModel.status == "HUMAN_INTERVENTION_REQUIRED",
        )
        .all()
    )

    return {
        "investigation_id": str(investigation.id),
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
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    if investigation.status in {"COMPLETED", "RUNNING"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume investigation from status: {investigation.status}"
        )

    from app.models.research_task import ResearchTask as ResearchTaskModel
    from app.services.audit import record_event

    # 1. Fetch blocked tasks and set status to PENDING, clear intervention reason/type
    blocked_tasks = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == investigation.id,
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
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


    # 3. Log INVESTIGATION_RESUMED event
    record_event(
        db,
        investigation.id,
        "INVESTIGATION_RESUMED",
        "browser",
        "STARTED",
        {"resumed_tasks_count": len(blocked_tasks)}
    )

    # 4. Reconstruct graph state and trigger execution
    from app.graph.workflow import app as graph_app
    from app.services.investigation import recover_investigation_state, serialize_state

    state = recover_investigation_state(db, investigation.id)
    state["status"] = "PENDING_RESEARCH"
    state["stop_reason"] = None
    if state.get("pending_tasks"):
        for t in state["pending_tasks"]:
            if getattr(t, "status", None) == "HUMAN_INTERVENTION_REQUIRED":
                t.status = "PENDING"

    investigation.persistent_graph_state = serialize_state(state)
    try:
        db.commit()
    except Exception:
        db.rollback()

    # Execute graph
    graph_app.invoke(state)

    # Return updated investigation status
    db.refresh(investigation)
    return {
        "id": str(investigation.id),
        "status": investigation.status,
    }


@router.get("/{investigation_id}/events/stream")
async def stream_investigation_events(
    investigation_id: str,
    once: bool = False,
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    import asyncio
    import json
    import uuid
    from app.models.investigation import Investigation as InvestigationModel
    from app.models.investigation_event import InvestigationEvent as InvestigationEventModel

    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")

    investigation = db.query(InvestigationModel).filter(InvestigationModel.id == inv_uuid).first()
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    async def event_generator():
        sent_event_ids = set()
        while True:
            events = (
                db.query(InvestigationEventModel)
                .filter(InvestigationEventModel.investigation_id == inv_uuid)
                .order_by(InvestigationEventModel.created_at.asc())
                .all()
            )
            for event in events:
                if event.id not in sent_event_ids:
                    sent_event_ids.add(event.id)
                    event_data = {
                        "id": str(event.id),
                        "investigation_id": str(event.investigation_id),
                        "event_type": event.event_type,
                        "node": event.node,
                        "status": event.status,
                        "metadata": json.loads(event.metadata_json),
                        "created_at": event.created_at.isoformat() if event.created_at else None,
                    }
                    yield f"data: {json.dumps(event_data)}\n\n"
            
            if once:
                break

            db.expire_all()
            inv = db.query(InvestigationModel).filter(InvestigationModel.id == inv_uuid).first()
            if inv and inv.status in {"COMPLETED", "FAILED"}:
                await asyncio.sleep(2)
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{investigation_id}/tasks/{task_id}/human-intervention")
def complete_human_intervention(
    investigation_id: str,
    task_id: str,
    db: Session = Depends(get_db),
) -> dict:
    from app.models.investigation import Investigation as InvestigationModel
    from app.models.research_task import ResearchTask as ResearchTaskModel
    from app.services.audit import record_event
    import uuid

    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")

    investigation = db.query(InvestigationModel).filter(InvestigationModel.id == inv_uuid).first()
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    task = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == inv_uuid,
            ResearchTaskModel.task_id == task_id,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Research task not found")

    if task.status != "HUMAN_INTERVENTION_REQUIRED":
        return {
            "status": "success",
            "message": f"Task is already resumed or completed. Current status: {task.status}",
            "task_id": task_id,
        }

    record_event(
        db,
        inv_uuid,
        "HUMAN_ACTION_COMPLETED",
        "browser",
        "IN_PROGRESS",
        {
            "task_id": task_id,
            "message": "Human verification completed. Resuming research..."
        }
    )

    task.status = "PENDING"
    task.intervention_type = None
    task.intervention_reason = None
    task.started_at = None
    task.completed_at = None

    other_blocked = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == inv_uuid,
            ResearchTaskModel.status == "HUMAN_INTERVENTION_REQUIRED",
        )
        .first()
    )

    if not other_blocked:
        investigation.status = "PENDING_RESEARCH"

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    from app.graph.workflow import app as graph_app
    from app.services.investigation import recover_investigation_state, serialize_state

    state = recover_investigation_state(db, investigation.id)
    state["status"] = "PENDING_RESEARCH"
    state["stop_reason"] = None
    if state.get("pending_tasks"):
        for t in state["pending_tasks"]:
            if t.task_id == task_id:
                t.status = "PENDING"

    investigation.persistent_graph_state = serialize_state(state)
    try:
        db.commit()
    except Exception:
        db.rollback()

    graph_app.invoke(state)

    db.refresh(investigation)
    db.refresh(task)
    return {
        "status": "success",
        "investigation_status": investigation.status,
        "task_status": task.status,
    }


@router.get("/{investigation_id}/history")
def get_investigation_history(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.evidence import get_evidences_for_investigation
    from app.services.research_task import get_research_tasks_for_investigation
    from app.models.report import Report
    from app.models.investigation_event import InvestigationEvent

    evidences = get_evidences_for_investigation(db, investigation.id)
    tasks = get_research_tasks_for_investigation(db, investigation.id)

    latest_report = (
        db.query(Report)
        .filter(Report.investigation_id == investigation.id)
        .order_by(Report.version.desc())
        .first()
    )

    events = (
        db.query(InvestigationEvent)
        .filter(InvestigationEvent.investigation_id == investigation.id)
        .order_by(InvestigationEvent.created_at.asc())
        .all()
    )

    return {
        "id": str(investigation.id),
        "status": investigation.status,
        "current_node": investigation.current_node,
        "retry_count": investigation.retry_count,
        "risk_score": investigation.risk_score,
        "risk_level": investigation.risk_level,
        "resolved_entity_id": str(investigation.resolved_entity_id) if investigation.resolved_entity_id else None,
        "entity_confidence": investigation.entity_confidence,
        "input_data": json.loads(investigation.input_data) if investigation.input_data else {},
        "raw_input": json.loads(investigation.raw_input) if investigation.raw_input else {},
        "normalized_input": json.loads(investigation.normalized_input) if investigation.normalized_input else {},
        "tasks": [
            {
                "id": str(t.id),
                "task_id": t.task_id,
                "task_type": t.task_type,
                "target": t.target,
                "objective": t.objective,
                "status": t.status,
                "intervention_type": t.intervention_type,
            }
            for t in tasks
        ],
        "evidence_count": len(evidences),
        "evidence_ids": [ev.research_result_id for ev in evidences],
        "latest_report_version": latest_report.version if latest_report else None,
        "event_count": len(events),
        "created_at": investigation.created_at.isoformat() if investigation.created_at else None,
        "completed_at": investigation.completed_timestamp.isoformat() if investigation.completed_timestamp else None,
        "updated_at": investigation.updated_at.isoformat() if investigation.updated_at else None,
    }


def _build_report_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=== ENTITY OVERVIEW ==="])
    entity = report.get("entity") or {}
    writer.writerow(["Business Name", entity.get("business_name") or entity.get("name") or "N/A"])
    writer.writerow(["GSTIN", entity.get("gstin") or "N/A"])
    writer.writerow(["CIN", entity.get("cin") or "N/A"])
    writer.writerow(["Website", entity.get("website") or "N/A"])
    writer.writerow(["Entity Confidence", report.get("entity_confidence", 0.0)])
    writer.writerow([])

    writer.writerow(["=== RISK SUMMARY ==="])
    overall = report.get("overall_risk") or {}
    writer.writerow(["Overall Risk Score", overall.get("score", 0)])
    writer.writerow(["Overall Risk Level", overall.get("level", "UNKNOWN")])
    writer.writerow(["Recommendation", report.get("recommendation", "")])
    writer.writerow([])

    writer.writerow(["=== CATEGORY SCORES ==="])
    writer.writerow(["Category", "Score"])
    for cat, score in (report.get("category_scores") or {}).items():
        writer.writerow([cat, score])
    writer.writerow([])

    writer.writerow(["=== MAJOR FINDINGS ==="])
    writer.writerow(["Code", "Category", "Severity", "Description", "Confidence", "Risk Weight", "Evidence IDs"])
    for finding in (report.get("major_findings") or []):
        ev_ids = ", ".join(finding.get("evidence_ids") or [])
        writer.writerow([
            finding.get("code"),
            finding.get("category"),
            finding.get("severity"),
            finding.get("description"),
            finding.get("confidence"),
            finding.get("risk_weight"),
            ev_ids,
        ])
    writer.writerow([])

    writer.writerow(["=== EVIDENCE SUMMARY ==="])
    writer.writerow(["Evidence ID", "Task ID", "Field Name", "Source Name", "Source URL", "Retrieved At", "Confidence"])
    for ev in (report.get("evidence_summary") or []):
        writer.writerow([
            ev.get("evidence_id"),
            ev.get("task_id"),
            ev.get("field_name"),
            ev.get("source_name"),
            ev.get("source_url"),
            ev.get("retrieved_at"),
            ev.get("confidence"),
        ])

    return output.getvalue()


@router.get("/{investigation_id}/export")
def export_investigation_report(
    format: str = "json",
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
):
    from app.api.investigations import get_investigation_report as fetch_report

    report = fetch_report(investigation=investigation, db=db)
    fmt = format.lower().strip()

    if fmt == "csv":
        csv_content = _build_report_csv(report)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="report_{investigation.id}.csv"'},
        )
    else:
        json_content = json.dumps(report, indent=2)
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="report_{investigation.id}.json"'},
        )


@router.get("/{investigation_id}/export/json")
def export_investigation_report_json(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
):
    return export_investigation_report(format="json", investigation=investigation, db=db)


@router.get("/{investigation_id}/export/csv")
def export_investigation_report_csv(
    investigation: Investigation = Depends(get_owned_investigation),
    db: Session = Depends(get_db),
):
    return export_investigation_report(format="csv", investigation=investigation, db=db)


test_router = APIRouter(tags=["testing"])

from pydantic import BaseModel, Field


class IntakeTestRequest(BaseModel):
    business_name: str | None = None
    gstin: str | None = None
    cin: str | None = None
    epfo_code: str | None = None
    website: str | None = None
    location: str | None = None
    people: list[str] | None = Field(default_factory=list)


class IntakeTestResponse(BaseModel):
    business_name: str | None = None
    gstin: str | None = None
    cin: str | None = None
    epfo_code: str | None = None
    website: str | None = None
    location: str | None = None
    people: list[str] | None = Field(default_factory=list)


@test_router.post("/test/intake", response_model=IntakeTestResponse)
def test_intake(payload: IntakeTestRequest) -> dict:
    from app.agents.intake import IntakeAgent
    agent = IntakeAgent()
    return agent.process(payload.model_dump())


class DiscoveryTestRequest(BaseModel):
    business_name: str | None = None
    gstin: str | None = None
    cin: str | None = None
    website: str | None = None
    location: str | None = None


class DiscoveryCandidateTest(BaseModel):
    name: str | None = None
    gstin: str | None = None
    cin: str | None = None
    website: str | None = None
    location: str | None = None
    confidence: float


class DiscoveryTestResponse(BaseModel):
    candidate_entities: list[DiscoveryCandidateTest] = []


@test_router.post("/test/entity-discovery", response_model=DiscoveryTestResponse)
def test_entity_discovery(payload: DiscoveryTestRequest) -> dict:
    from app.agents.discovery import DiscoveryAgent
    agent = DiscoveryAgent()
    return agent.process(payload.model_dump())


class PlannerResearchTaskSchema(BaseModel):
    task_id: str
    task_type: str
    target: str
    objective: str
    required_fields: list[str]
    priority: int
    preferred_sources: list[str] = Field(default_factory=list)
    fallback_sources: list[str] = Field(default_factory=list)
    allowed_domains: list[str] | None = None
    status: str = "PENDING"


from typing import Any


class PlannerResearchResultSchema(BaseModel):
    result_id: str
    task_id: str
    field_name: str
    field_value: Any
    source_name: str
    source_url: str | None = None
    retrieved_at: str
    confidence: float
    evidence_basis: str | None = None


class PlannerTestRequest(BaseModel):
    pending_tasks: list[PlannerResearchTaskSchema] = Field(default_factory=list)
    completed_tasks: list[PlannerResearchTaskSchema] = Field(default_factory=list)
    failed_tasks: list[PlannerResearchTaskSchema] = Field(default_factory=list)
    results: list[PlannerResearchResultSchema] = Field(default_factory=list)
    raw_input: dict[str, Any] = Field(default_factory=dict)
    normalized_input: dict[str, Any] = Field(default_factory=dict)


class PlannerTestResponse(BaseModel):
    new_tasks: list[PlannerResearchTaskSchema]


@test_router.post("/test/planner", response_model=PlannerTestResponse)
def test_planner(payload: PlannerTestRequest) -> dict:
    from app.agents.planner import PlannerAgent
    from app.graph.state import ResearchTask as GraphTask, ResearchResult as GraphResult
    
    pending_tasks = [GraphTask(**t.model_dump()) for t in payload.pending_tasks]
    completed_tasks = [GraphTask(**t.model_dump()) for t in payload.completed_tasks]
    failed_tasks = [GraphTask(**t.model_dump()) for t in payload.failed_tasks]
    results = [GraphResult(**r.model_dump()) for r in payload.results]
    
    state = {
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "results": results,
        "raw_input": payload.raw_input,
        "normalized_input": payload.normalized_input,
    }
    
    agent = PlannerAgent()
    new_tasks = agent.plan(state)
    
    return {"new_tasks": [PlannerResearchTaskSchema(**t.model_dump()) for t in new_tasks]}


class BrowserTestRequest(BaseModel):
    task_id: str
    task_type: str
    target: str
    objective: str
    required_fields: list[str]
    priority: int
    preferred_sources: list[str] = Field(default_factory=list)
    fallback_sources: list[str] = Field(default_factory=list)
    allowed_domains: list[str] | None = None
    status: str = "PENDING"


class BrowserTestResult(BaseModel):
    result_id: str
    task_id: str
    field_name: str
    field_value: Any
    source_name: str
    source_url: str | None = None
    retrieved_at: str
    confidence: float
    evidence_basis: str | None = None


class BrowserTestResponse(BaseModel):
    results: list[BrowserTestResult]
    browser_status: str
    error: str | None = None


@test_router.post("/test/browser-research", response_model=BrowserTestResponse)
def test_browser_research(payload: BrowserTestRequest) -> dict:
    from app.agents.browser import BrowserResearchAgent
    from app.graph.state import ResearchTask as GraphTask
    from app.core.exceptions import HumanInterventionRequiredException
    
    task = GraphTask(**payload.model_dump())
    agent = BrowserResearchAgent()
    
    try:
        results = agent.execute(task, investigation_id=None)
        mapped_results = []
        for r in results:
            item = r.model_dump()
            if item.get("field_value") in {"NOT_FOUND", "UNAVAILABLE"}:
                item["confidence"] = 0.0
            mapped_results.append(BrowserTestResult(**item))
            
        return {
            "results": mapped_results,
            "browser_status": "SUCCESS",
            "error": None
        }
    except HumanInterventionRequiredException as e:
        return {
            "results": [],
            "browser_status": "BLOCKED",
            "error": str(e)
        }
    except Exception as e:
        return {
            "results": [],
            "browser_status": "ERROR",
            "error": str(e)
        }


@router.get("/{investigation_id}/tasks/{task_id}/browser-session")
def get_task_browser_session(investigation_id: str, task_id: str):
    import uuid
    from app.core.browser_session_manager import browser_session_manager
    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")
        
    session = browser_session_manager.get_session(inv_uuid, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active browser session found for this task")
        
    return {
        "session_id": str(session.id),
        "status": session.status,
        "created_at": session.created_at.isoformat(),
        "last_activity_at": session.last_activity_at.isoformat(),
        "current_url": session.get_url(),
    }


@router.get("/{investigation_id}/tasks/{task_id}/screenshot")
def get_task_screenshot(investigation_id: str, task_id: str):
    import uuid
    from fastapi.responses import Response
    from app.core.browser_session_manager import browser_session_manager
    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")
        
    import os
    # 1. If active in-memory session exists, try live screenshot first
    session = browser_session_manager.get_session(inv_uuid, task_id)
    if session:
        try:
            screenshot_bytes = session.screenshot()
            if screenshot_bytes:
                try:
                    os.makedirs("/tmp/bizrisk_screenshots", exist_ok=True)
                    with open(f"/tmp/bizrisk_screenshots/{inv_uuid}_{task_id}.png", "wb") as f:
                        f.write(screenshot_bytes)
                except Exception:
                    pass
                return Response(content=screenshot_bytes, media_type="image/png")
        except Exception:
            pass

    # 2. Check deterministic persisted disk location
    saved_path = f"/tmp/bizrisk_screenshots/{inv_uuid}_{task_id}.png"
    if os.path.exists(saved_path):
        try:
            with open(saved_path, "rb") as f:
                content = f.read()
                if content:
                    return Response(content=content, media_type="image/png")
        except Exception:
            pass

    # 3. Check database BrowserSession metadata for persisted screenshot path
    try:
        import json
        from app.db.session import SessionLocal, db_lock
        from app.models.browser_session import BrowserSession
        with db_lock:
            with SessionLocal() as db:
                bs = db.query(BrowserSession).filter(
                    BrowserSession.investigation_id == inv_uuid,
                    BrowserSession.task_id == task_id
                ).order_by(BrowserSession.started_at.desc()).first()
                if bs and bs.failure_reason:
                    try:
                        meta = json.loads(bs.failure_reason)
                        custom_path = meta.get("screenshot_path")
                        if custom_path and os.path.exists(custom_path):
                            with open(custom_path, "rb") as f:
                                return Response(content=f.read(), media_type="image/png")
                    except Exception:
                        pass
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="No active browser session or saved screenshot found for this task")


@router.post("/{investigation_id}/tasks/{task_id}/click")
def post_task_click(investigation_id: str, task_id: str, payload: dict):
    import uuid
    from app.core.browser_session_manager import browser_session_manager
    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")
        
    session = browser_session_manager.get_session(inv_uuid, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active browser session found for this task")
        
    x = payload.get("x")
    y = payload.get("y")
    if x is None or y is None:
        raise HTTPException(status_code=400, detail="Click coordinates x and y are required")
        
    try:
        session.click(float(x), float(y))
        return {"status": "success", "message": f"Clicked at ({x}, {y})"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to click: {e}")


@router.post("/{investigation_id}/tasks/{task_id}/type")
def post_task_type(investigation_id: str, task_id: str, payload: dict):
    import uuid
    from app.core.browser_session_manager import browser_session_manager
    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")
        
    session = browser_session_manager.get_session(inv_uuid, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active browser session found for this task")
        
    text = payload.get("text")
    if text is None:
        raise HTTPException(status_code=400, detail="Text field is required")
        
    try:
        session.type(str(text))
        return {"status": "success", "message": "Typed text successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to type: {e}")


@router.post("/{investigation_id}/tasks/{task_id}/clear")
def post_task_clear(investigation_id: str, task_id: str):
    import uuid
    from app.core.browser_session_manager import browser_session_manager
    try:
        inv_uuid = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid investigation UUID")
        
    session = browser_session_manager.get_session(inv_uuid, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active browser session found for this task")
        
    try:
        session.clear()
        return {"status": "success", "message": "Cleared input successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear input: {e}")





