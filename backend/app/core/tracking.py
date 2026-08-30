from contextvars import ContextVar
from typing import Optional, Any
from app.graph.state import InvestigationState

# Context variables for resource tracking
llm_calls_var: ContextVar[int] = ContextVar("llm_calls", default=0)
token_usage_var: ContextVar[int] = ContextVar("token_usage", default=0)
browser_actions_var: ContextVar[int] = ContextVar("browser_actions", default=0)
browser_tasks_count_var: ContextVar[int] = ContextVar("browser_tasks_count", default=0)


def init_tracking_from_state(state: InvestigationState) -> None:
    llm_calls_var.set(state.get("llm_calls", 0))
    token_usage_var.set(state.get("token_usage", 0))
    browser_actions_var.set(state.get("browser_actions", 0))
    browser_tasks_count_var.set(state.get("browser_tasks_count", 0))


def update_state_from_tracking(state: InvestigationState) -> dict:
    updated = dict(state)
    updated["llm_calls"] = llm_calls_var.get()
    updated["token_usage"] = token_usage_var.get()
    updated["browser_actions"] = browser_actions_var.get()
    updated["browser_tasks_count"] = browser_tasks_count_var.get()
    return updated


def check_limits(state: InvestigationState, extra_tasks: int = 0, extra_actions: int = 0) -> Optional[str]:
    from app.core.config import get_settings
    settings = get_settings()

    if state.get("status") == "WAITING_FOR_USER":
        return None

    if state.get("stop_reason") and state.get("status") in {"LIMIT_REACHED", "MAX_LOOPS_REACHED"}:
        return state["stop_reason"]

    # Enforce maximum search/research depth (loop count)
    if state.get("planner_loop_count", 0) >= settings.max_research_depth:
        return "Max research depth reached"

    # Enforce maximum browser/research tasks
    if browser_tasks_count_var.get() + extra_tasks > settings.max_research_tasks:
        return "Max browser/research tasks limit reached"

    # Enforce maximum browser actions
    if browser_actions_var.get() + extra_actions > settings.max_browser_actions:
        return "Max browser actions limit reached"

    # Enforce maximum LLM calls
    if llm_calls_var.get() >= settings.max_llm_calls:
        return "Max LLM calls limit reached"

    # Enforce token budget
    if token_usage_var.get() >= settings.token_budget:
        return "Token budget exhausted"

    return None
