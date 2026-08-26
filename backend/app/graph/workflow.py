from langgraph.graph import END, StateGraph

from app.graph.edges import (
    should_continue,
    should_continue_after_resolution,
)
from app.graph.nodes import (
    browser_node,
    discovery_node,
    entity_resolution_node,
    intake_node,
    planner_node,
    risk_analysis_node,
)
from app.graph.state import InvestigationState


workflow = StateGraph(InvestigationState)

workflow.add_node("intake", intake_node)
workflow.add_node("discovery", discovery_node)
workflow.add_node("planner", planner_node)
workflow.add_node("browser", browser_node)
workflow.add_node("entity_resolution", entity_resolution_node)
workflow.add_node("risk_analysis", risk_analysis_node)

workflow.set_entry_point("intake")

workflow.add_edge("intake", "discovery")
workflow.add_edge("discovery", "planner")

workflow.add_conditional_edges(
    "planner",
    should_continue,
    {
        "browser": "browser",
        "__end__": "risk_analysis",
    },
)

workflow.add_edge("browser", "entity_resolution")

workflow.add_conditional_edges(
    "entity_resolution",
    should_continue_after_resolution,
    {
        "planner": "planner",
        "__end__": "risk_analysis",
    },
)

workflow.add_edge("risk_analysis", END)

app = workflow.compile()
