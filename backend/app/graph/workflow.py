from langgraph.graph import END, StateGraph

from app.graph.edges import should_continue
from app.graph.nodes import (
    browser_node,
    discovery_node,
    intake_node,
    planner_node,
)
from app.graph.state import InvestigationState


workflow = StateGraph(InvestigationState)

workflow.add_node("intake", intake_node)
workflow.add_node("discovery", discovery_node)
workflow.add_node("planner", planner_node)
workflow.add_node("browser", browser_node)

workflow.set_entry_point("intake")

workflow.add_edge("intake", "discovery")
workflow.add_edge("discovery", "planner")

workflow.add_conditional_edges(
    "planner",
    should_continue,
    {
        "browser": "browser",
        "__end__": END,
    },
)

workflow.add_edge("browser", "planner")

app = workflow.compile()
