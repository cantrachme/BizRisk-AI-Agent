from app.graph.state import InvestigationState, MAX_PLANNER_LOOPS


def should_continue(state: InvestigationState) -> str:
    loop_count = state.get("planner_loop_count", 0)

    if loop_count >= MAX_PLANNER_LOOPS:
        return "__end__"

    pending_tasks = state.get("pending_tasks") or []

    if pending_tasks:
        return "browser"

    return "__end__"
