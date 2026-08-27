from typing import List, Dict, Any
import uuid
from app.graph.state import InvestigationState, ResearchTask, ResearchResult

class PlannerAgent:
    """
    Deterministic Planner Agent for the initial MVP foundation.
    Evaluates the current state (inputs and results collected so far) and determines
    what missing or unverified research requirements need to be scheduled.
    """
    def __init__(self, llm=None, prompt_version: str = "v1") -> None:
        from app.core.llm import get_llm_provider
        from app.core.prompts import load_prompt
        self.llm = llm or get_llm_provider(temperature=0.0)
        self.prompt_version = prompt_version
        self.prompt = load_prompt("planner", prompt_version)

    def plan(self, state: InvestigationState) -> List[ResearchTask]:
        # Initialize lists from state
        pending_tasks = state.get("pending_tasks") or []
        completed_tasks = state.get("completed_tasks") or []
        failed_tasks = state.get("failed_tasks") or []
        results = state.get("results") or []
        
        # Combine inputs
        raw_input = state.get("raw_input") or {}
        normalized_input = state.get("normalized_input") or {}
        
        gstin = normalized_input.get("gstin") or raw_input.get("gstin")
        cin = normalized_input.get("cin") or raw_input.get("cin")
        website = normalized_input.get("website") or raw_input.get("website")
        business_name = normalized_input.get("business_name") or raw_input.get("business_name")
        location = normalized_input.get("location") or raw_input.get("location")

        # Track what has already been scheduled or completed
        scheduled_task_types = {t.task_type for t in pending_tasks + completed_tasks + failed_tasks}
        completed_task_types = {t.task_type for t in completed_tasks}
        
        new_tasks: List[ResearchTask] = []

        # Helper to generate unique task IDs
        def next_task_id() -> str:
            total = len(completed_tasks) + len(failed_tasks) + len(pending_tasks) + len(new_tasks)
            return f"TASK-{total + 1:03d}"

        # 1. Process candidate entities discovered in previous tasks
        discovered_gstin = None
        discovered_cin = None
        discovered_website = None

        for res in results:
            if res.field_name == "candidate_entities" and isinstance(res.field_value, list):
                # Look for the highest confidence candidate that has identifiers
                candidates = sorted(res.field_value, key=lambda x: x.get("confidence", 0), reverse=True)
                if candidates:
                    best = candidates[0]
                    discovered_gstin = best.get("gstin")
                    discovered_cin = best.get("cin")
                    discovered_website = best.get("website")
                    break

        # Resolve targets (favor raw/normalized input, fallback to discovered values)
        target_gstin = gstin or discovered_gstin
        target_cin = cin or discovered_cin
        target_website = website or discovered_website

        # 2. Schedule Entity Discovery if no specific identifiers are available
        if not target_gstin and not target_cin:
            if "ENTITY_DISCOVERY" not in scheduled_task_types:
                target_str = ""
                if business_name:
                    target_str += business_name
                if location:
                    target_str += f" in {location}" if target_str else location
                
                if target_str:
                    new_tasks.append(
                        ResearchTask(
                            task_id=next_task_id(),
                            task_type="ENTITY_DISCOVERY",
                            target=target_str,
                            objective="Search public records to discover matching legal entities, GSTIN, and CIN.",
                            required_fields=["candidate_entities"],
                            priority=1,
                            preferred_sources=["generic_web"],
                            fallback_sources=[]
                        )
                    )

        # 3. Schedule GST Verification if target GSTIN is known but not verified
        if target_gstin:
            if "GST_VERIFICATION" not in scheduled_task_types:
                new_tasks.append(
                    ResearchTask(
                        task_id=next_task_id(),
                        task_type="GST_VERIFICATION",
                        target=target_gstin,
                        objective=f"Verify GSTIN {target_gstin} and retrieve registration status and details.",
                        required_fields=["legal_name", "gst_status", "registered_address", "business_activity"],
                        priority=1,
                        preferred_sources=["gst.gov.in"],
                        fallback_sources=["third_party"]
                    )
                )

        # 4. Schedule MCA Verification if target CIN is known but not verified
        if target_cin:
            if "MCA_VERIFICATION" not in scheduled_task_types:
                new_tasks.append(
                    ResearchTask(
                        task_id=next_task_id(),
                        task_type="MCA_VERIFICATION",
                        target=target_cin,
                        objective=f"Verify CIN {target_cin} and retrieve company registration details.",
                        required_fields=["legal_name", "company_status", "incorporation_date", "registered_address"],
                        priority=1,
                        preferred_sources=["mca.gov.in"],
                        fallback_sources=["third_party"]
                    )
                )

        # 5. Schedule Website Verification if target website is known but not verified
        if target_website:
            if "WEBSITE_VERIFICATION" not in scheduled_task_types:
                new_tasks.append(
                    ResearchTask(
                        task_id=next_task_id(),
                        task_type="WEBSITE_VERIFICATION",
                        target=target_website,
                        objective=f"Analyze company website {target_website} to verify business claims and contact details.",
                        required_fields=["website_status", "contact_address", "established_year"],
                        priority=2,
                        preferred_sources=["company_website"],
                        fallback_sources=["generic_web"]
                    )
                )

        return new_tasks
