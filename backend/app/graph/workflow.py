from langgraph.graph import END, StateGraph

from app.db.base import Base  # noqa: F401
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
    report_generation_node,
    qa_node,
)
from app.graph.state import InvestigationState


def should_continue_after_qa(state: InvestigationState) -> str:
    qa_res = state.get("qa_result") or {}
    loop_count = state.get("qa_loop_count") or 0
    if qa_res.get("status") == "FAIL" and loop_count < 2:
        issues = qa_res.get("issues", [])
        issue_types = {issue.get("type") for issue in issues if isinstance(issue, dict)}
        
        # Route according to failure type
        if "WRONG_ENTITY" in issue_types:
            return "entity_resolution"
        if "MISSING_EVIDENCE" in issue_types or "UNSUPPORTED_CLAIM" in issue_types:
            return "planner"
        if "WRONG_RISK_SCORE" in issue_types:
            return "risk_analysis"
        if "REPORT_WORDING" in issue_types:
            return "report_generation"
        return "planner"
    return "__end__"


workflow = StateGraph(InvestigationState)

workflow.add_node("intake", intake_node)
workflow.add_node("discovery", discovery_node)
workflow.add_node("planner", planner_node)
workflow.add_node("browser", browser_node)
workflow.add_node("entity_resolution", entity_resolution_node)
workflow.add_node("risk_analysis", risk_analysis_node)
workflow.add_node("report_generation", report_generation_node)
workflow.add_node("qa", qa_node)

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

workflow.add_edge("risk_analysis", "report_generation")
workflow.add_edge("report_generation", "qa")

workflow.add_conditional_edges(
    "qa",
    should_continue_after_qa,
    {
        "planner": "planner",
        "entity_resolution": "entity_resolution",
        "risk_analysis": "risk_analysis",
        "report_generation": "report_generation",
        "__end__": END,
    },
)

app = workflow.compile()
