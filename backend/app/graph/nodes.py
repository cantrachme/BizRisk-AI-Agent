from datetime import datetime, timezone
import uuid

from app.agents.discovery import DiscoveryAgent
from app.agents.intake import IntakeAgent
from app.agents.planner import PlannerAgent
from app.entity_resolution.resolver import resolve_entity
from app.graph.state import (
    InvestigationState,
    MAX_PLANNER_LOOPS,
    ResearchResult,
)


def update_investigation_in_db(
    investigation_id_str: str | None,
    current_node: str,
    status: str | None = None,
    retry_count: int | None = None,
    risk_score: int | None = None,
    risk_level: str | None = None,
    resolved_entity_id: uuid.UUID | None = None,
    entity_confidence: float | None = None,
    completed: bool = False,
):
    if not investigation_id_str:
        return
    try:
        investigation_id = uuid.UUID(str(investigation_id_str))
    except ValueError:
        return

    from app.db.session import SessionLocal
    from app.models.investigation import Investigation
    from datetime import datetime, timezone

    with SessionLocal() as db:
        inv = db.get(Investigation, investigation_id)
        if inv:
            inv.current_node = current_node
            if status:
                inv.status = status
            if retry_count is not None:
                inv.retry_count = retry_count
            if risk_score is not None:
                inv.risk_score = risk_score
            if risk_level:
                inv.risk_level = risk_level
            if resolved_entity_id is not None:
                inv.resolved_entity_id = resolved_entity_id
            if entity_confidence is not None:
                inv.entity_confidence = entity_confidence
            if completed:
                inv.completed_at = datetime.now(timezone.utc)
            db.commit()


def log_node_event(
    investigation_id: uuid.UUID | None,
    event_type: str,
    node: str,
    status: str,
    metadata: dict = None,
):
    if not investigation_id:
        from app.services.audit import record_event
        record_event(None, uuid.UUID(int=0), event_type, node, status, metadata)
        return

    from app.db.session import SessionLocal
    from app.services.audit import record_event
    with SessionLocal() as db:
        record_event(db, investigation_id, event_type, node, status, metadata)


def intake_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "INVESTIGATION_STARTED", "intake", "STARTED")
    log_node_event(investigation_id, "NODE_STARTED", "intake", "STARTED")

    try:
        normalized_input = IntakeAgent().process(state.get("raw_input") or {})
        update_investigation_in_db(state.get("investigation_id"), "intake", status="NORMALIZED")
        log_node_event(investigation_id, "NODE_COMPLETED", "intake", "COMPLETED")
        return {
            "normalized_input": normalized_input,
            "status": "NORMALIZED",
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "intake",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "intake",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            state.get("investigation_id"),
            "intake",
            status="FAILED",
            completed=True
        )
        raise


def discovery_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "discovery", "STARTED")

    try:
        discovery = DiscoveryAgent().process(
            state.get("normalized_input") or {}
        )

        candidates = discovery.get("candidate_entities", [])

        if not candidates:
            update_investigation_in_db(state.get("investigation_id"), "discovery", status="DISCOVERY_COMPLETED")
            log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "COMPLETED", {"candidates_count": 0})
            return {
                "results": [],
                "status": "DISCOVERY_COMPLETED",
            }

        result = ResearchResult(
            result_id="DISCOVERY-001",
            task_id="DISCOVERY",
            field_name="candidate_entities",
            field_value=candidates,
            source_name="discovery_agent",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=max(
                candidate.get("confidence", 0.0)
                for candidate in candidates
            ),
        )

        if investigation_id_str:
            try:
                investigation_id_val = uuid.UUID(str(investigation_id_str))
                from app.db.session import SessionLocal
                from app.services.evidence import save_research_result
                with SessionLocal() as db:
                    save_research_result(db, result, investigation_id_val)
            except ValueError:
                pass

        update_investigation_in_db(investigation_id_str, "discovery", status="DISCOVERY_COMPLETED")
        log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "COMPLETED", {"candidates_count": len(candidates)})
        return {
            "results": [result],
            "status": "DISCOVERY_COMPLETED",
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "discovery",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "discovery",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            investigation_id_str,
            "discovery",
            status="FAILED",
            completed=True
        )
        raise


def planner_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "planner", "STARTED")

    try:
        current_loops = state.get("planner_loop_count", 0)

        if current_loops >= MAX_PLANNER_LOOPS:
            update_investigation_in_db(state.get("investigation_id"), "planner", status="MAX_LOOPS_REACHED", retry_count=state.get("qa_loop_count", 0))
            log_node_event(investigation_id, "NODE_COMPLETED", "planner", "COMPLETED", {"status": "MAX_LOOPS_REACHED"})
            return {
                "pending_tasks": [],
                "status": "MAX_LOOPS_REACHED",
            }

        new_tasks = PlannerAgent().plan(state)
        current_loops += 1

        existing_pending = state.get("pending_tasks") or []
        updated_pending = existing_pending + new_tasks

        if current_loops >= MAX_PLANNER_LOOPS:
            status = "MAX_LOOPS_REACHED"
        elif new_tasks:
            status = "PENDING_RESEARCH"
        else:
            status = "COMPLETED"

        update_investigation_in_db(state.get("investigation_id"), "planner", status=status, retry_count=state.get("qa_loop_count", 0))
        log_node_event(investigation_id, "NODE_COMPLETED", "planner", "COMPLETED", {"status": status, "new_tasks_count": len(new_tasks)})
        return {
            "pending_tasks": updated_pending,
            "planner_loop_count": current_loops,
            "status": status,
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "planner",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "planner",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            state.get("investigation_id"),
            "planner",
            status="FAILED",
            completed=True
        )
        raise


def browser_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "browser_research", "STARTED")

    try:
        from app.agents.browser import BrowserResearchAgent

        agent = BrowserResearchAgent()

        pending_tasks = state.get("pending_tasks") or []
        existing_completed = state.get("completed_tasks") or []
        existing_failed = state.get("failed_tasks") or []
        existing_results = state.get("results") or []

        completed_tasks = list(existing_completed)
        failed_tasks = list(existing_failed)
        results = list(existing_results)
        new_results = []

        for task in pending_tasks:
            task_results = agent.execute(task)

            if task_results:
                completed_tasks.append(
                    task.model_copy(update={"status": "COMPLETED"})
                )
                results.extend(task_results)
                new_results.extend(task_results)
            else:
                failed_tasks.append(
                    task.model_copy(update={"status": "FAILED"})
                )

        if investigation_id_str and new_results:
            try:
                investigation_id_val = uuid.UUID(str(investigation_id_str))
                from app.db.session import SessionLocal
                from app.services.evidence import save_research_results
                with SessionLocal() as db:
                    save_research_results(db, new_results, investigation_id_val)
            except ValueError:
                pass

        update_investigation_in_db(investigation_id_str, "browser", status="RESEARCH_COMPLETED")
        log_node_event(investigation_id, "NODE_COMPLETED", "browser_research", "COMPLETED", {"new_results_count": len(new_results)})
        return {
            "pending_tasks": [],
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "results": results,
            "status": "RESEARCH_COMPLETED",
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "browser_research",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "browser_research",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            investigation_id_str,
            "browser",
            status="FAILED",
            completed=True
        )
        raise



def entity_resolution_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "entity_resolution", "STARTED")

    try:
        normalized_input = state.get("normalized_input") or {}
        results = state.get("results") or []

        candidates = []

        for result in results:
            if result.field_name == "candidate_entities":
                candidates.extend(result.field_value or [])

        resolution = resolve_entity(
            normalized_input,
            candidates,
        )

        status = "ENTITY_RESOLVED" if resolution["matched"] else "ENTITY_UNRESOLVED"
        resolved_entity_id = None
        entity = resolution.get("entity")
        if entity:
            name_val = entity.get("business_name") or entity.get("name")
            if name_val:
                resolved_entity_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(name_val))

        update_investigation_in_db(
            state.get("investigation_id"),
            "entity_resolution",
            status=status,
            resolved_entity_id=resolved_entity_id,
            entity_confidence=resolution["confidence"],
        )

        log_node_event(investigation_id, "NODE_COMPLETED", "entity_resolution", "COMPLETED", {"status": status, "confidence": resolution["confidence"]})

        if resolution["matched"]:
            return {
                "resolved_entity": resolution["entity"],
                "entity_confidence": resolution["confidence"],
                "entity_resolution_status": resolution["match_type"],
                "status": "ENTITY_RESOLVED",
            }

        return {
            "resolved_entity": resolution["entity"],
            "entity_confidence": resolution["confidence"],
            "entity_resolution_status": resolution["match_type"],
            "status": "ENTITY_UNRESOLVED",
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "entity_resolution",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "entity_resolution",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            state.get("investigation_id"),
            "entity_resolution",
            status="FAILED",
            completed=True
        )
        raise


def risk_analysis_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "risk_analysis", "STARTED")

    try:
        if investigation_id:
            from app.services.risk_analysis import analyze_investigation
            from app.db.session import SessionLocal
            with SessionLocal() as db:
                analysis = analyze_investigation(db, investigation_id)
        else:
            # Fallback to local memory-only calculation for non-UUID / dummy IDs in graph tests
            from app.risk.engine import calculate_risk_analysis
            results = state.get("results") or []
            analysis = calculate_risk_analysis(results)

        update_investigation_in_db(
            investigation_id_str,
            "risk_analysis",
            risk_score=analysis["overall_risk"]["score"],
            risk_level=analysis["overall_risk"]["level"]
        )

        log_node_event(investigation_id, "NODE_COMPLETED", "risk_analysis", "COMPLETED", {"score": analysis["overall_risk"]["score"], "level": analysis["overall_risk"]["level"]})

        return {
            "overall_risk": analysis["overall_risk"],
            "category_scores": analysis["category_scores"],
            "risk_signals": analysis["risk_signals"],
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "risk_analysis",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "risk_analysis",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            investigation_id_str,
            "risk_analysis",
            status="FAILED",
            completed=True
        )
        raise


def report_generation_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "report_generation", "STARTED")

    try:
        if investigation_id:
            from app.services.report import generate_investigation_report
            from app.db.session import SessionLocal
            with SessionLocal() as db:
                report = generate_investigation_report(db, investigation_id)
        else:
            # Fallback to local memory-only report generation for non-UUID / dummy IDs in graph tests
            from app.risk.engine import calculate_risk_analysis
            from app.services.report import generate_recommendation
            results = state.get("results") or []
            analysis = calculate_risk_analysis(results)

            report = {
                "entity": state.get("resolved_entity") or {},
                "entity_confidence": state.get("entity_confidence") or 0.0,
                "overall_risk": {
                    "score": analysis["overall_risk"]["score"],
                    "level": analysis["overall_risk"]["level"],
                },
                "category_scores": analysis["category_scores"],
                "major_findings": [
                    {
                        "code": sig["code"],
                        "category": sig["category"],
                        "severity": sig["severity"],
                        "description": sig["description"],
                        "evidence_ids": sig["evidence_ids"],
                        "confidence": sig["confidence"],
                        "risk_weight": sig["risk_weight"],
                    }
                    for sig in analysis["risk_signals"]
                ],
                "positive_findings": [],
                "unverified_information": [],
                "recommendation": generate_recommendation(analysis["overall_risk"]["score"]),
                "evidence_summary": [
                    {
                        "evidence_id": res.result_id,
                        "task_id": res.task_id,
                        "field_name": res.field_name,
                        "field_value": res.field_value,
                        "source_name": res.source_name,
                        "source_url": res.source_url,
                        "retrieved_at": res.retrieved_at,
                        "confidence": res.confidence,
                    }
                    for res in results
                ],
                "meta": {
                    "rule_version": "1.0.0",
                    "report_version": "1.0.0",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            }

        update_investigation_in_db(investigation_id_str, "report_generation", status="REPORT_GENERATED")
        log_node_event(investigation_id, "NODE_COMPLETED", "report_generation", "COMPLETED")
        return {
            "report": report,
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "report_generation",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "report_generation",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            investigation_id_str,
            "report_generation",
            status="FAILED",
            completed=True
        )
        raise


def qa_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    log_node_event(investigation_id, "NODE_STARTED", "qa", "STARTED")

    try:
        qa_loop_count = state.get("qa_loop_count") or 0

        if investigation_id:
            from app.services.qa import validate_report
            from app.db.session import SessionLocal
            with SessionLocal() as db:
                qa_result = validate_report(db, investigation_id)
        else:
            # Fallback to local memory-only report validation for non-UUID / dummy IDs in graph tests
            report = state.get("report") or {}
            overall_risk = state.get("overall_risk") or {}
            report_score = report.get("overall_risk", {}).get("score", 0)
            engine_score = overall_risk.get("score", 0)

            score_verified = (report_score == engine_score)

            resolved_entity = state.get("resolved_entity") or {}
            entity_verified = bool(
                resolved_entity
                and (
                    resolved_entity.get("business_name")
                    or resolved_entity.get("name")
                    or resolved_entity.get("gstin")
                    or resolved_entity.get("cin")
                )
            )

            # Simple check for forbidden words in report findings description
            issues = []
            findings = report.get("major_findings") or []
            for finding in findings:
                desc = finding.get("description") or ""
                desc_lower = desc.lower()
                for word in ["fraud", "scam", "fake", "fraudster"]:
                    if word in desc_lower:
                        issues.append({
                            "type": "REPORT_WORDING",
                            "finding": f"Forbidden word '{word}' found in report."
                        })

            # Check for GST contradiction if dummy GST is inactive but evidence says active
            results = state.get("results") or []
            for finding in findings:
                if finding.get("code") == "GST_INACTIVE":
                    for res in results:
                        if res.field_name == "gst_status" and "active" in str(res.field_value).lower():
                            issues.append({
                                "type": "UNSUPPORTED_CLAIM",
                                "finding": "GST inactive contradicts active GST research result."
                            })

            status_str = "PASS" if (score_verified and entity_verified and not issues) else "FAIL"

            qa_result = {
                "status": status_str,
                "issues": issues,
                "evidence_coverage": 1.0 if findings else 0.0,
                "score_verified": score_verified,
                "entity_verified": entity_verified,
            }

        # Increment loop count if validation failed
        if qa_result["status"] == "FAIL":
            qa_loop_count += 1

        status = "COMPLETED" if qa_result["status"] == "PASS" or qa_loop_count >= 2 else "FAILED_QA"
        completed = (qa_result["status"] == "PASS" or qa_loop_count >= 2)

        update_investigation_in_db(
            investigation_id_str,
            "qa",
            status=status,
            retry_count=qa_loop_count,
            completed=completed
        )

        log_node_event(investigation_id, "NODE_COMPLETED", "qa", "COMPLETED", {"qa_status": qa_result["status"]})

        if qa_result["status"] == "FAIL":
            log_node_event(investigation_id, "QA_RETRY", "qa", "FAIL", {"retry_count": qa_loop_count})

        if completed:
            if qa_result["status"] == "PASS":
                log_node_event(investigation_id, "INVESTIGATION_COMPLETED", "qa", "COMPLETED")
            else:
                log_node_event(investigation_id, "INVESTIGATION_FAILED", "qa", "FAILED", {"reason": "Max QA retries reached"})

        return {
            "qa_result": qa_result,
            "qa_loop_count": qa_loop_count,
        }
    except Exception as e:
        log_node_event(
            investigation_id,
            "NODE_FAILED",
            "qa",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "retryable": False
            }
        )
        log_node_event(
            investigation_id,
            "INVESTIGATION_FAILED",
            "qa",
            "FAILED",
            {
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )
        update_investigation_in_db(
            investigation_id_str,
            "qa",
            status="FAILED",
            completed=True
        )
        raise
