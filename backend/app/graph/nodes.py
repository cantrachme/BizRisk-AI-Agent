from app.agents.planner import PlannerAgent
from app.graph.state import InvestigationState

def planner_node(state: InvestigationState) -> dict:
    """
    LangGraph node for executing the Planner Agent.
    Evaluates current state, generates any missing research tasks,
    increments the loop counter, and updates investigation status.
    """
    planner = PlannerAgent()
    new_tasks = planner.plan(state)
    
    # Increment loop counter
    current_loops = state.get("planner_loop_count", 0) + 1
    
    # Update pending tasks list
    existing_pending = state.get("pending_tasks") or []
    updated_pending = existing_pending + new_tasks
    
    # Determine the status based on loops and new tasks
    if current_loops > 3:
        # Cap at 3 loops strictly
        status = "MAX_LOOPS_REACHED"
        updated_pending = []  # Clear pending if loop capped
    elif new_tasks:
        status = "PENDING_RESEARCH"
    else:
        status = "COMPLETED"

    return {
        "pending_tasks": updated_pending,
        "planner_loop_count": current_loops,
        "status": status
    }
