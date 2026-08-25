from langgraph.graph import StateGraph, END
from app.graph.state import InvestigationState
from app.graph.nodes import planner_node
from app.graph.edges import should_continue

# Initialize StateGraph with our custom InvestigationState structure
workflow = StateGraph(InvestigationState)

# Add the planner node
workflow.add_node("planner", planner_node)

# Set the entry point of the graph to be the planner
workflow.set_entry_point("planner")

# Add conditional routing from the planner node
workflow.add_conditional_edges(
    "planner",
    should_continue,
    {
        "__end__": END
    }
)

# Compile the graph
app = workflow.compile()
