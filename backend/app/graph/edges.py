from app.graph.state import InvestigationState

def should_continue(state: InvestigationState) -> str:
    """
    Conditional routing logic.
    Determines next state. Since the Research Agent is external, the graph
    exits (`__end__`) to hand over pending tasks, or ends permanently if
    the loop count is reached or all tasks are finished.
    """
    loop_count = state.get("planner_loop_count", 0)
    
    # Cap strictly at 3 loops
    if loop_count >= 3:
        return "__end__"
        
    # If no pending tasks are returned, exit
    pending_tasks = state.get("pending_tasks") or []
    if not pending_tasks:
        return "__end__"

    # Handoff to external research execution
    return "__end__"
