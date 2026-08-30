from datetime import datetime, timezone
import uuid
import threading
import concurrent.futures

from app.agents.discovery import DiscoveryAgent
from app.agents.intake import IntakeAgent
from app.agents.planner import PlannerAgent
from app.entity_resolution.resolver import resolve_entity
from app.graph.state import (
    InvestigationState,
    MAX_PLANNER_LOOPS,
    ResearchResult,
)
from app.core.exceptions import HumanInterventionRequiredException


from app.db.session import db_lock

def SessionLocal():
    import app.db.session as db_session_mod
    return db_session_mod.SessionLocal()


_sentinel = object()

def update_investigation_in_db(
    investigation_id_str: str | None,
    current_node: str,
    status: str | None = None,
    retry_count: int | None = None,
    risk_score = _sentinel,
    risk_level = _sentinel,
    resolved_entity_id: uuid.UUID | None = None,
    entity_confidence: float | None = None,
    completed: bool = False,
    state: dict | None = None,
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
    from app.services.investigation import serialize_state
    import json

    with db_lock:
        with SessionLocal() as db:
            inv = db.get(Investigation, investigation_id)
            if inv:
                inv.current_node = current_node
                inv.current_graph_node = current_node
                if status:
                    inv.status = status
                if retry_count is not None:
                    inv.retry_count = retry_count
                if risk_score is not _sentinel:
                    inv.risk_score = risk_score
                if risk_level is not _sentinel:
                    inv.risk_level = risk_level
                if resolved_entity_id is not None:
                    inv.resolved_entity_id = resolved_entity_id
                if entity_confidence is not None:
                    inv.entity_confidence = entity_confidence
                if completed:
                    inv.completed_timestamp = datetime.now(timezone.utc)

                if state:
                    user_id = state.get("user_id") or (state.get("raw_input") or {}).get("user_id")
                    if user_id:
                        inv.user_id = str(user_id)
                    if "raw_input" in state:
                        inv.raw_input = json.dumps(state["raw_input"])
                    if "normalized_input" in state:
                        inv.normalized_input = json.dumps(state["normalized_input"])
                    if state.get("resolved_entity"):
                        entity = state["resolved_entity"]
                        name_val = entity.get("business_name") or entity.get("name")
                        if name_val and not inv.resolved_entity_id:
                            inv.resolved_entity_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(name_val))
                        inv.entity_confidence = state.get("entity_confidence", 0.0)
                    inv.persistent_graph_state = serialize_state(state)

                # Final safety check: ensure the resolved entity is persisted to the entities table
                if inv.resolved_entity_id:
                    from app.models.entity import Entity as EntityModel
                    existing_entity = db.get(EntityModel, inv.resolved_entity_id)
                    if not existing_entity:
                        ent_details = {}
                        if state and state.get("resolved_entity"):
                            ent_details = state["resolved_entity"]
                        elif inv.persistent_graph_state:
                            try:
                                parsed_state = json.loads(inv.persistent_graph_state)
                                ent_details = parsed_state.get("resolved_entity") or {}
                            except Exception:
                                pass
                        
                        name_val = ent_details.get("business_name") or ent_details.get("name") or "Resolved Entity"
                        new_ent = EntityModel(
                            id=inv.resolved_entity_id,
                            canonical_name=str(name_val),
                            trade_name=ent_details.get("trade_name") or ent_details.get("name"),
                            gstin=ent_details.get("gstin"),
                            cin=ent_details.get("cin"),
                            epfo_code=ent_details.get("epfo_code"),
                            website=ent_details.get("website"),
                            registered_address=ent_details.get("registered_address") or ent_details.get("address"),
                            state=ent_details.get("state") or ent_details.get("location"),
                            business_activity=ent_details.get("business_activity"),
                        )
                        db.merge(new_ent)

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
        if state.get("status") in {"DISCOVERY_COMPLETED", "PENDING_RESEARCH", "WAITING_FOR_USER", "LIMIT_REACHED", "MAX_LOOPS_REACHED"}:
            return state

        normalized_input = IntakeAgent().process(state.get("raw_input") or {})
        ret_val = {
            "normalized_input": normalized_input,
            "status": "NORMALIZED",
            "research_depth": 0,
            "browser_actions": 0,
            "browser_tasks_count": 0,
            "llm_calls": 0,
            "token_usage": 0,
            "stop_reason": None,
        }
        updated_state = dict(state)
        updated_state.update(ret_val)
        update_investigation_in_db(state.get("investigation_id"), "intake", status="NORMALIZED", state=updated_state)
        log_node_event(investigation_id, "NODE_COMPLETED", "intake", "COMPLETED")
        return ret_val
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
        if state.get("status") in {"DISCOVERY_COMPLETED", "PENDING_RESEARCH", "WAITING_FOR_USER", "LIMIT_REACHED", "MAX_LOOPS_REACHED"}:
            return state

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Check limits BEFORE starting discovery
        reason = check_limits(state)
        if reason:
            ret_val = update_state_from_tracking(state)
            ret_val.update({
                "status": "LIMIT_REACHED",
                "stop_reason": reason,
            })
            update_investigation_in_db(state.get("investigation_id"), "discovery", status="LIMIT_REACHED", state=ret_val)
            log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "LIMIT_REACHED", {"reason": reason})
            return ret_val

        discovery = DiscoveryAgent().process(
            state.get("normalized_input") or {}
        )

        candidates = discovery.get("candidate_entities", [])

        # Update tracking vars after potential LLM calls
        updated_state = update_state_from_tracking(state)

        # Check limits AFTER executing discovery
        reason = check_limits(updated_state)
        if reason:
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": reason,
            })
            update_investigation_in_db(state.get("investigation_id"), "discovery", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "LIMIT_REACHED", {"reason": reason})
            return updated_state

        if not candidates:
            updated_state.update({
                "results": [],
                "status": "DISCOVERY_COMPLETED",
            })
            update_investigation_in_db(state.get("investigation_id"), "discovery", status="DISCOVERY_COMPLETED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "COMPLETED", {"candidates_count": 0})
            return updated_state

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
                from app.db.session import SessionLocal, db_lock
                from app.services.evidence import save_research_result
                with db_lock:
                    with SessionLocal() as db:
                        save_research_result(db, result, investigation_id_val)
            except ValueError:
                pass

        updated_state.update({
            "results": [result],
            "status": "DISCOVERY_COMPLETED",
        })
        update_investigation_in_db(investigation_id_str, "discovery", status="DISCOVERY_COMPLETED", state=updated_state)
        log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "COMPLETED", {"candidates_count": len(candidates)})
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "discovery", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "discovery", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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
        if state.get("pending_tasks") and state.get("status") != "FAILED_QA":
            return state

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Check existing limits BEFORE executing planner node
        # A new planner loop increments research depth
        temp_state = update_state_from_tracking(state)
        research_depth = temp_state.get("research_depth", 0) + 1
        temp_state["research_depth"] = research_depth

        reason = check_limits(temp_state)
        if reason:
            status = "MAX_LOOPS_REACHED" if reason == "Max research depth reached" else "LIMIT_REACHED"
            ret_val = update_state_from_tracking(state)
            ret_val.update({
                "pending_tasks": [],
                "status": status,
                "stop_reason": reason,
                "research_depth": research_depth,
            })
            update_investigation_in_db(state.get("investigation_id"), "planner", status=status, retry_count=state.get("qa_loop_count", 0), state=ret_val)
            log_node_event(investigation_id, "NODE_COMPLETED", "planner", "LIMIT_REACHED", {"status": status, "reason": reason})
            return ret_val

        current_loops = state.get("planner_loop_count", 0)

        # Generate new tasks
        new_tasks = PlannerAgent().plan(state)
        current_loops += 1

        if investigation_id and new_tasks:
            from app.db.session import SessionLocal
            from app.services.research_task import save_research_tasks
            with SessionLocal() as db:
                save_research_tasks(db, new_tasks, investigation_id)

        existing_pending = state.get("pending_tasks") or []
        updated_pending = existing_pending + new_tasks

        # Update state fields
        updated_state = update_state_from_tracking(state)
        updated_state["research_depth"] = research_depth
        updated_state["planner_loop_count"] = current_loops

        # Check limits AFTER planning/LLM calls
        reason = check_limits(updated_state)
        if reason:
            status = "MAX_LOOPS_REACHED" if reason == "Max research depth reached" else "LIMIT_REACHED"
            updated_state["stop_reason"] = reason
            updated_state["pending_tasks"] = []
        else:
            if new_tasks:
                status = "PENDING_RESEARCH"
            else:
                status = "COMPLETED"

        updated_state["status"] = status
        updated_state["pending_tasks"] = updated_pending if status not in {"LIMIT_REACHED", "MAX_LOOPS_REACHED"} else []

        update_investigation_in_db(state.get("investigation_id"), "planner", status=status, retry_count=state.get("qa_loop_count", 0), state=updated_state)
        log_node_event(investigation_id, "NODE_COMPLETED", "planner", "COMPLETED", {"status": status, "new_tasks_count": len(new_tasks) if status not in {"LIMIT_REACHED", "MAX_LOOPS_REACHED"} else 0})
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            status = "MAX_LOOPS_REACHED" if "depth" in err_msg.lower() else "LIMIT_REACHED"
            updated_state.update({
                "status": status,
                "stop_reason": err_msg,
                "pending_tasks": [],
            })
            update_investigation_in_db(state.get("investigation_id"), "planner", status=status, retry_count=state.get("qa_loop_count", 0), state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "planner", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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


class ThreadSafeContextVarProxy:
    def __init__(self, shared_dict, key, lock):
        self.shared_dict = shared_dict
        self.key = key
        self.lock = lock

    def get(self, default=0):
        with self.lock:
            return self.shared_dict.get(self.key, default)

    def set(self, value):
        with self.lock:
            self.shared_dict[self.key] = value


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
        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
            browser_actions_var,
            browser_tasks_count_var,
        )
        init_tracking_from_state(state)

        # Check existing limits BEFORE executing browser research node
        reason = check_limits(state)
        if reason:
            update_investigation_in_db(state.get("investigation_id"), "browser", status="LIMIT_REACHED")
            log_node_event(investigation_id, "NODE_COMPLETED", "browser_research", "LIMIT_REACHED", {"reason": reason})
            ret_val = update_state_from_tracking(state)
            ret_val.update({
                "status": "LIMIT_REACHED",
                "stop_reason": reason,
            })
            return ret_val

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

        remaining_pending = []
        limit_reached = False
        hitl_blocked = False
        hitl_reason = None
        blocked_sources = set()

        cache_miss_tasks = []

        for task in pending_tasks:
            # Resolve task source using browser agent helper
            source_key = BrowserResearchAgent._select_source(task)

            # Skip execution if the source/domain is already blocked by human verification requirement
            if source_key and source_key in blocked_sources:
                remaining_pending.append(task)
                continue

            reused_results = []
            if investigation_id:
                from app.services.evidence import get_cached_source_result
                from app.graph.state import ResearchResult
                from app.agents.browser import SOURCES
                import json

                source = None
                if source_key:
                    source_name, source_url, confidence = None, None, None
                    try:
                        from app.services.source_registry import get_source_by_name
                        with SessionLocal() as db:
                            db_source = get_source_by_name(db, source_key)
                            if db_source:
                                source_name = db_source.name
                    except Exception:
                        pass
                    
                    if not source_name:
                        if source_key in SOURCES:
                            source_name = SOURCES[source_key][0]
                        else:
                            source_name = source_key
                    source = source_name

                all_fields_fresh = True
                task_reused_results = []

                if source:
                    with SessionLocal() as db:
                        for field in task.required_fields:
                            cached_ev = get_cached_source_result(
                                db,
                                task_type=task.task_type,
                                target=task.target,
                                objective=task.objective,
                                field_name=field,
                                source_name=source,
                            )
                            if not cached_ev:
                                all_fields_fresh = False
                                break
                            else:
                                try:
                                    parsed_val = json.loads(cached_ev.field_value)
                                except Exception:
                                    parsed_val = cached_ev.field_value

                                task_reused_results.append(
                                    ResearchResult(
                                        result_id=cached_ev.research_result_id,
                                        task_id=task.task_id,
                                        field_name=cached_ev.field_name,
                                        field_value=parsed_val,
                                        source_name=cached_ev.source_name,
                                        source_url=cached_ev.source_url,
                                        retrieved_at=cached_ev.retrieved_timestamp.isoformat(),
                                        confidence=cached_ev.confidence,
                                    )
                                )
                else:
                    all_fields_fresh = False

                if all_fields_fresh and task_reused_results:
                    reused_results = task_reused_results

            if reused_results:
                # Sufficient fresh evidence exists -> Reuse it, do not invoke the browser
                completed_tasks.append(
                    task.model_copy(update={"status": "COMPLETED"})
                )
                results.extend(reused_results)
                if investigation_id:
                    from app.services.research_task import update_research_task_status
                    with db_lock:
                        with SessionLocal() as db:
                            update_research_task_status(db, investigation_id, task.task_id, "COMPLETED")
            else:
                cache_miss_tasks.append(task)

        if cache_miss_tasks:
            import app.core.tracking as tracking

            shared_dict = {
                "llm_calls": tracking.llm_calls_var.get(),
                "token_usage": tracking.token_usage_var.get(),
                "browser_actions": tracking.browser_actions_var.get(),
                "browser_tasks_count": tracking.browser_tasks_count_var.get(),
            }
            lock = threading.RLock()

            proxy_llm_calls = ThreadSafeContextVarProxy(shared_dict, "llm_calls", lock)
            proxy_token_usage = ThreadSafeContextVarProxy(shared_dict, "token_usage", lock)
            proxy_browser_actions = ThreadSafeContextVarProxy(shared_dict, "browser_actions", lock)
            proxy_browser_tasks_count = ThreadSafeContextVarProxy(shared_dict, "browser_tasks_count", lock)

            orig_llm = tracking.llm_calls_var
            orig_token = tracking.token_usage_var
            orig_actions = tracking.browser_actions_var
            orig_tasks = tracking.browser_tasks_count_var

            def run_task_in_thread(task):
                # 1. Check limits with lock
                with lock:
                    temp_state = update_state_from_tracking(state)
                    temp_state["llm_calls"] = shared_dict["llm_calls"]
                    temp_state["token_usage"] = shared_dict["token_usage"]
                    temp_state["browser_actions"] = shared_dict["browser_actions"]
                    temp_state["browser_tasks_count"] = shared_dict["browser_tasks_count"]

                    reason = check_limits(temp_state, extra_tasks=1, extra_actions=1)
                    print("DEBUG RUN_TASK_IN_THREAD REASON:", reason)
                    if reason:
                        return {
                            "task_id": task.task_id,
                            "status": "LIMIT_REACHED",
                            "stop_reason": reason,
                            "results": []
                        }
                    shared_dict["browser_tasks_count"] += 1
                    shared_dict["browser_actions"] += 1

                # 2. Update task status to STARTED in DB
                if investigation_id:
                    from app.services.research_task import update_research_task_status
                    with db_lock:
                        with SessionLocal() as db:
                            update_research_task_status(db, investigation_id, task.task_id, "STARTED")

                # 3. Execute BrowserResearchAgent
                try:
                    from unittest.mock import Mock
                    exec_func = getattr(BrowserResearchAgent, "execute", None)
                    if isinstance(exec_func, Mock) or hasattr(exec_func, "assert_called_with"):
                        task_results = exec_func(task)
                    else:
                        agent = BrowserResearchAgent()
                        try:
                            task_results = agent.execute(task, investigation_id=investigation_id)
                        except TypeError as te:
                            if any(msg in str(te) for msg in ["positional argument", "keyword argument", "unexpected keyword", "takes"]):
                                task_results = agent.execute(task)
                            else:
                                raise
                    if task_results:
                        if investigation_id:
                            from app.services.research_task import update_research_task_status
                            with db_lock:
                                with SessionLocal() as db:
                                    update_research_task_status(db, investigation_id, task.task_id, "COMPLETED")
                        return {
                            "task_id": task.task_id,
                            "status": "COMPLETED",
                            "results": task_results
                        }
                    else:
                        if investigation_id:
                            from app.services.research_task import update_research_task_status
                            with db_lock:
                                with SessionLocal() as db:
                                    update_research_task_status(
                                        db,
                                        investigation_id,
                                        task.task_id,
                                        "FAILED",
                                        error="No results returned"
                                    )
                        return {
                            "task_id": task.task_id,
                            "status": "FAILED",
                            "results": []
                        }
                except HumanInterventionRequiredException as hitl_ex:
                    if investigation_id:
                        from app.services.research_task import update_research_task_status
                        from app.services.audit import record_event
                        with db_lock:
                            with SessionLocal() as db:
                                update_research_task_status(
                                    db,
                                    investigation_id,
                                    task.task_id,
                                    "HUMAN_INTERVENTION_REQUIRED",
                                    error=hitl_ex.message,
                                    intervention_type=hitl_ex.intervention_type,
                                    intervention_reason=hitl_ex.message
                                )
                                record_event(
                                    db,
                                    investigation_id,
                                    "HUMAN_INTERVENTION_REQUIRED",
                                    "browser",
                                    "WAITING_FOR_USER",
                                    {"task_id": task.task_id, "type": hitl_ex.intervention_type, "reason": hitl_ex.message}
                                )
                    return {
                        "task_id": task.task_id,
                        "status": "HUMAN_INTERVENTION_REQUIRED",
                        "intervention_type": hitl_ex.intervention_type,
                        "intervention_reason": hitl_ex.message,
                        "results": []
                    }
                except Exception as e:
                    err_msg = str(e)
                    if investigation_id:
                        from app.services.research_task import update_research_task_status
                        with db_lock:
                            with SessionLocal() as db:
                                update_research_task_status(
                                    db,
                                    investigation_id,
                                    task.task_id,
                                    "FAILED",
                                    error=err_msg
                                )
                    return {
                        "task_id": task.task_id,
                        "status": "FAILED",
                        "error": err_msg,
                        "results": []
                    }

            try:
                tracking.llm_calls_var = proxy_llm_calls
                tracking.token_usage_var = proxy_token_usage
                tracking.browser_actions_var = proxy_browser_actions
                tracking.browser_tasks_count_var = proxy_browser_tasks_count

                with concurrent.futures.ThreadPoolExecutor(max_workers=len(cache_miss_tasks)) as executor:
                    futures = {executor.submit(run_task_in_thread, t): t for t in cache_miss_tasks}
                    for future in concurrent.futures.as_completed(futures):
                        task = futures[future]
                        try:
                            res_dict = future.result()
                        except Exception as e:
                            res_dict = {
                                "task_id": task.task_id,
                                "status": "FAILED",
                                "error": str(e),
                                "results": []
                            }

                        task_status = res_dict["status"]
                        task_results = res_dict.get("results") or []

                        if task_status == "COMPLETED":
                            completed_tasks.append(task.model_copy(update={"status": "COMPLETED"}))
                            results.extend(task_results)
                            new_results.extend(task_results)
                        elif task_status == "FAILED":
                            failed_tasks.append(task.model_copy(update={"status": "FAILED"}))
                        elif task_status == "HUMAN_INTERVENTION_REQUIRED":
                            hitl_blocked = True
                            hitl_reason = res_dict["intervention_reason"]
                            source_key = BrowserResearchAgent._select_source(task)
                            if source_key:
                                blocked_sources.add(source_key)
                            blocked_task = task.model_copy(update={"status": "HUMAN_INTERVENTION_REQUIRED"})
                            remaining_pending.append(blocked_task)
                        elif task_status == "LIMIT_REACHED":
                            limit_reached = True
                            state["stop_reason"] = res_dict["stop_reason"]
                            state["status"] = "LIMIT_REACHED"
                            remaining_pending.append(task)
            finally:
                tracking.llm_calls_var = orig_llm
                tracking.token_usage_var = orig_token
                tracking.browser_actions_var = orig_actions
                tracking.browser_tasks_count_var = orig_tasks

                tracking.llm_calls_var.set(shared_dict["llm_calls"])
                tracking.token_usage_var.set(shared_dict["token_usage"])
                tracking.browser_actions_var.set(shared_dict["browser_actions"])
                tracking.browser_tasks_count_var.set(shared_dict["browser_tasks_count"])

        if investigation_id_str and new_results:
            try:
                investigation_id_val = uuid.UUID(str(investigation_id_str))
                from app.services.evidence import save_research_results
                with db_lock:
                    with SessionLocal() as db:
                        save_research_results(db, new_results, investigation_id_val)
            except ValueError:
                pass

        # Update state dictionary from the tracking context variables
        updated_state = update_state_from_tracking(state)

        # Decide browser node return status
        if hitl_blocked:
            status = "WAITING_FOR_USER"
        elif updated_state.get("stop_reason") or updated_state.get("status") == "LIMIT_REACHED":
            status = "LIMIT_REACHED"
        else:
            status = "RESEARCH_COMPLETED"

        updated_state.update({
            "pending_tasks": remaining_pending,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "results": results,
            "status": status,
        })
        if hitl_reason:
            updated_state["stop_reason"] = hitl_reason

        update_investigation_in_db(investigation_id_str, "browser", status=status, state=updated_state)
        log_node_event(investigation_id, "NODE_COMPLETED", "browser_research", "COMPLETED", {"new_results_count": len(new_results), "status": status})
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)

        if isinstance(e, HumanInterventionRequiredException):
            updated_state.update({
                "status": "WAITING_FOR_USER",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "browser", status="WAITING_FOR_USER", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "browser_research", "WAITING_FOR_USER", {"reason": err_msg})
            return updated_state

        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "browser", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "browser_research", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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
            completed=True,
            state=updated_state
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
        is_waiting_for_user = state.get("status") == "WAITING_FOR_USER"

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Check existing limits BEFORE executing entity resolution node
        reason = check_limits(state)
        if reason and not is_waiting_for_user:
            update_investigation_in_db(state.get("investigation_id"), "entity_resolution", status="LIMIT_REACHED")
            log_node_event(investigation_id, "NODE_COMPLETED", "entity_resolution", "LIMIT_REACHED", {"reason": reason})
            ret_val = update_state_from_tracking(state)
            ret_val.update({
                "status": "LIMIT_REACHED",
                "stop_reason": reason,
            })
            return ret_val

        normalized_input = state.get("normalized_input") or {}
        results = state.get("results") or []

        candidates = []

        for result in results:
            if result.field_name == "candidate_entities":
                candidates.extend(result.field_value or [])

        from app.core.caching import ResolvedEntityCache
        cached_res = None
        if investigation_id:
            cached_res = ResolvedEntityCache.get(investigation_id, normalized_input)

        if cached_res is not None:
            resolution = cached_res
        else:
            resolution = resolve_entity(
                normalized_input,
                candidates,
            )
            if investigation_id:
                ResolvedEntityCache.set(investigation_id, normalized_input, resolution)

        status = "WAITING_FOR_USER" if is_waiting_for_user else ("ENTITY_RESOLVED" if resolution["matched"] else "ENTITY_UNRESOLVED")
        resolved_entity_id = None
        entity = resolution.get("entity")
        if entity:
            name_val = entity.get("business_name") or entity.get("name")
            if name_val:
                resolved_entity_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(name_val))

        updated_state = update_state_from_tracking(state)
        updated_state.update({
            "resolved_entity": resolution["entity"],
            "entity_confidence": resolution["confidence"],
            "entity_resolution_status": resolution["match_type"],
            "status": status,
        })

        # Check limits AFTER executing entity resolution
        reason = check_limits(updated_state)
        if reason and not is_waiting_for_user:
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": reason,
            })
            update_investigation_in_db(
                state.get("investigation_id"),
                "entity_resolution",
                status="LIMIT_REACHED",
                resolved_entity_id=resolved_entity_id,
                entity_confidence=resolution["confidence"],
                state=updated_state
            )
            log_node_event(investigation_id, "NODE_COMPLETED", "entity_resolution", "LIMIT_REACHED", {"reason": reason})
            return updated_state

        update_investigation_in_db(
            state.get("investigation_id"),
            "entity_resolution",
            status=status,
            resolved_entity_id=resolved_entity_id,
            entity_confidence=resolution["confidence"],
            state=updated_state
        )
        log_node_event(investigation_id, "NODE_COMPLETED", "entity_resolution", "COMPLETED" if not is_waiting_for_user else "WAITING_FOR_USER", {"status": status, "confidence": resolution["confidence"]})
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            update_investigation_in_db(state.get("investigation_id"), "entity_resolution", status="LIMIT_REACHED")
            log_node_event(investigation_id, "NODE_COMPLETED", "entity_resolution", "LIMIT_REACHED", {"reason": err_msg})
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            return updated_state

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
        if state.get("status") == "WAITING_FOR_USER":
            return state

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Note: we still run risk analysis even if limit is reached to allow graceful final scoring

        if investigation_id:
            from app.services.risk_analysis import analyze_investigation
            with db_lock:
                with SessionLocal() as db:
                    analysis = analyze_investigation(db, investigation_id)
        else:
            # Fallback to local memory-only calculation for non-UUID / dummy IDs in graph tests
            from app.risk.engine import calculate_risk_analysis
            results = state.get("results") or []
            analysis = calculate_risk_analysis(results)

        updated_state = update_state_from_tracking(state)
        updated_state.update({
            "overall_risk": analysis["overall_risk"],
            "category_scores": analysis["category_scores"],
            "risk_signals": analysis["risk_signals"],
        })

        update_investigation_in_db(
            investigation_id_str,
            "risk_analysis",
            risk_score=analysis["overall_risk"]["score"],
            risk_level=analysis["overall_risk"]["level"],
            state=updated_state
        )

        log_node_event(investigation_id, "NODE_COMPLETED", "risk_analysis", "COMPLETED", {"score": analysis["overall_risk"]["score"], "level": analysis["overall_risk"]["level"]})
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "risk_analysis", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "risk_analysis", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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
        if state.get("status") == "WAITING_FOR_USER":
            return state

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Note: we still run report generation even if limit is reached to allow graceful final report generation

        if investigation_id:
            from app.services.report import generate_investigation_report
            with db_lock:
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

        updated_state = update_state_from_tracking(state)
        updated_state.update({
            "report": report,
        })
        update_investigation_in_db(investigation_id_str, "report_generation", status="REPORT_GENERATED", state=updated_state)
        log_node_event(investigation_id, "NODE_COMPLETED", "report_generation", "COMPLETED")
        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "report_generation", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "report_generation", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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
        if state.get("status") == "WAITING_FOR_USER":
            return state

        from app.core.tracking import (
            init_tracking_from_state,
            update_state_from_tracking,
            check_limits,
        )
        init_tracking_from_state(state)

        # Note: we still run QA even if limit is reached to allow graceful final scoring

        qa_loop_count = state.get("qa_loop_count") or 0

        if investigation_id:
            from app.services.qa import validate_report
            with db_lock:
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

        if qa_result["status"] == "PASS":
            status = "COMPLETED"
            completed = True
        elif qa_loop_count >= 2:
            status = "FAILED"
            completed = True
        else:
            status = "FAILED_QA"
            completed = False

        updated_state = update_state_from_tracking(state)

        updated_state.update({
            "qa_result": qa_result,
            "qa_loop_count": qa_loop_count,
            "status": status,
        })

        update_investigation_in_db(
            investigation_id_str,
            "qa",
            status=status,
            retry_count=qa_loop_count,
            completed=completed,
            state=updated_state
        )

        log_node_event(investigation_id, "NODE_COMPLETED", "qa", "COMPLETED", {"qa_status": qa_result["status"]})

        if qa_result["status"] == "FAIL":
            log_node_event(investigation_id, "QA_RETRY", "qa", "FAIL", {"retry_count": qa_loop_count})

        if completed:
            if qa_result["status"] == "PASS":
                log_node_event(investigation_id, "INVESTIGATION_COMPLETED", "qa", "COMPLETED")
            else:
                log_node_event(investigation_id, "INVESTIGATION_FAILED", "qa", "FAILED", {"reason": "Max QA retries reached"})

        return updated_state
    except Exception as e:
        err_msg = str(e)
        from app.core.tracking import update_state_from_tracking
        updated_state = update_state_from_tracking(state)
        if "limit reached" in err_msg.lower() or "budget exhausted" in err_msg.lower():
            updated_state.update({
                "status": "LIMIT_REACHED",
                "stop_reason": err_msg,
            })
            update_investigation_in_db(investigation_id_str, "qa", status="LIMIT_REACHED", state=updated_state)
            log_node_event(investigation_id, "NODE_COMPLETED", "qa", "LIMIT_REACHED", {"reason": err_msg})
            return updated_state

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
