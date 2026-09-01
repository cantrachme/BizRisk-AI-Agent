from app.graph.state import InvestigationState, MAX_PLANNER_LOOPS


def should_continue(state: InvestigationState) -> str:
    if state.get("stop_reason") or state.get("status") in {"LIMIT_REACHED", "MAX_LOOPS_REACHED", "WAITING_FOR_USER"}:
        return "__end__"

    from app.core.config import get_settings
    settings = get_settings()
    loop_count = state.get("planner_loop_count", 0)

    if loop_count >= settings.max_research_depth:
        return "__end__"

    pending_tasks = state.get("pending_tasks") or []

    if pending_tasks:
        return "browser"

    return "__end__"


def should_continue_after_resolution(
    state: InvestigationState,
) -> str:
    if state.get("stop_reason") or state.get("status") in {"LIMIT_REACHED", "MAX_LOOPS_REACHED", "WAITING_FOR_USER"}:
        return "__end__"

    from app.core.config import get_settings
    settings = get_settings()
    loop_count = state.get("planner_loop_count", 0)

    if loop_count >= settings.max_research_depth:
        return "__end__"

    return "planner"
