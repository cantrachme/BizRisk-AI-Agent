from typing import List, Dict, Any
import uuid
from app.graph.state import InvestigationState, ResearchTask, ResearchResult

class PlannerAgent:
    """
    Deterministic Multi-Source Planner Agent.
    Evaluates current inputs and results to plan research tasks across all required
    source categories: GST, MCA, EPFO, Company Website, Third-Party, and General Web.
    Also performs second-hop discovery when new identifiers are identified during research.
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
        epfo_code = normalized_input.get("epfo_code") or raw_input.get("epfo_code")
        website = normalized_input.get("website") or raw_input.get("website")
        business_name = normalized_input.get("business_name") or raw_input.get("business_name")
        location = normalized_input.get("location") or raw_input.get("location")

        # Track existing scheduled tasks by (type, normalized_target, preferred_sources)
        all_existing_tasks = pending_tasks + completed_tasks + failed_tasks
        scheduled_keys = {
            (t.task_type, t.target.strip().lower(), tuple(sorted(t.preferred_sources or [])))
            for t in all_existing_tasks if t.target
        }
        scheduled_task_types = {t.task_type for t in all_existing_tasks}
        
        new_tasks: List[ResearchTask] = []

        # Helper to generate unique task IDs
        def next_task_id() -> str:
            total = len(all_existing_tasks) + len(new_tasks)
            return f"TASK-{total + 1:03d}"

        # 1. Process candidate entities and discovered fields from previous research
        discovered_gstin = None
        discovered_cin = None
        discovered_epfo_code = None
        discovered_website = None
        discovered_legal_name = None

        for res in results:
            if res.field_name == "candidate_entities" and isinstance(res.field_value, list):
                candidates = sorted(res.field_value, key=lambda x: x.get("confidence", 0) if isinstance(x, dict) else 0, reverse=True)
                for cand in candidates:
                    if isinstance(cand, dict):
                        discovered_gstin = discovered_gstin or cand.get("gstin")
                        discovered_cin = discovered_cin or cand.get("cin")
                        discovered_epfo_code = discovered_epfo_code or cand.get("epfo_code")
                        discovered_website = discovered_website or cand.get("website")
                        discovered_legal_name = discovered_legal_name or cand.get("name")
            elif res.field_name in {"cin", "company_cin"} and res.field_value and str(res.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                discovered_cin = discovered_cin or str(res.field_value).strip()
            elif res.field_name in {"gstin", "tax_id"} and res.field_value and str(res.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                discovered_gstin = discovered_gstin or str(res.field_value).strip()
            elif res.field_name in {"epfo_code", "establishment_code"} and res.field_value and str(res.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                discovered_epfo_code = discovered_epfo_code or str(res.field_value).strip()
            elif res.field_name in {"website", "domain", "company_url"} and res.field_value and str(res.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                discovered_website = discovered_website or str(res.field_value).strip()
            elif res.field_name in {"legal_name", "company_name"} and res.field_value and str(res.field_value).strip().upper() not in {"NOT_FOUND", "UNAVAILABLE", "NONE"}:
                discovered_legal_name = discovered_legal_name or str(res.field_value).strip()

        # Resolve targets (favor raw/normalized input, fallback to discovered values)
        target_gstin = gstin or discovered_gstin
        target_cin = cin or discovered_cin
        target_epfo = epfo_code or discovered_epfo_code
        target_website = website or discovered_website
        primary_name = discovered_legal_name or business_name

        # Fetch sources from centralized source registry
        from app.research.source_registry import source_registry
        from app.db.session import SessionLocal
        from app.services.source_registry import get_preferred_sources

        gst_pref, gst_fall = source_registry.get_preferred_and_fallback_sources("GST_VERIFICATION")
        mca_pref, mca_fall = source_registry.get_preferred_and_fallback_sources("MCA_VERIFICATION")
        epfo_pref, epfo_fall = source_registry.get_preferred_and_fallback_sources("EPFO_VERIFICATION")
        web_pref, web_fall = source_registry.get_preferred_and_fallback_sources("WEBSITE_VERIFICATION")
        disc_pref, disc_fall = source_registry.get_preferred_and_fallback_sources("ENTITY_DISCOVERY")
        
        all_tp_sources = source_registry.list_sources(task_type="THIRD_PARTY_RESEARCH", enabled_only=True)
        tp_directory_sources = [
            s for s in all_tp_sources if s.name not in {"generic_web", "third_party"}
        ]
        if not tp_directory_sources:
            tp_directory_sources = all_tp_sources

        try:
            with SessionLocal() as db:
                db_gst_pref, db_gst_fall = get_preferred_sources(db, "GST_VERIFICATION")
                if db_gst_pref:
                    gst_pref = ["gst.gov.in" if x == "GST Portal" else x for x in db_gst_pref]
                    gst_fall = ["third_party" if x == "Third-Party Source" else x for x in db_gst_fall]
                db_mca_pref, db_mca_fall = get_preferred_sources(db, "MCA_VERIFICATION")
                if db_mca_pref:
                    mca_pref, mca_fall = db_mca_pref, db_mca_fall
                db_epfo_pref, db_epfo_fall = get_preferred_sources(db, "EPFO_VERIFICATION")
                if db_epfo_pref:
                    epfo_pref, epfo_fall = db_epfo_pref, db_epfo_fall
                db_web_pref, db_web_fall = get_preferred_sources(db, "WEBSITE_VERIFICATION")
                if db_web_pref:
                    web_pref, web_fall = db_web_pref, db_web_fall
                db_disc_pref, db_disc_fall = get_preferred_sources(db, "ENTITY_DISCOVERY")
                if db_disc_pref:
                    disc_pref, disc_fall = db_disc_pref, db_disc_fall
                db_tp_pref, db_tp_fall = get_preferred_sources(db, "THIRD_PARTY_RESEARCH")
                if db_tp_pref:
                    db_sources = [source_registry.get_source(x) for x in db_tp_pref if source_registry.get_source(x)]
                    if db_sources:
                        tp_directory_sources = db_sources
        except Exception:
            pass

        # Helper to safely add idempotent tasks
        def add_task_if_not_scheduled(
            task_type: str,
            target: str,
            objective: str,
            required_fields: List[str],
            priority: int,
            preferred_sources: List[str],
            fallback_sources: List[str],
        ):
            if not target or not target.strip():
                return
            target_clean = target.strip()
            task_key = (task_type, target_clean.lower(), tuple(sorted(preferred_sources or [])))
            if task_key in scheduled_keys:
                return
            new_t = ResearchTask(
                task_id=next_task_id(),
                task_type=task_type,
                target=target_clean,
                objective=objective,
                required_fields=required_fields,
                priority=priority,
                preferred_sources=preferred_sources,
                fallback_sources=fallback_sources,
            )
            new_tasks.append(new_t)
            scheduled_keys.add(task_key)
            scheduled_task_types.add(task_type)

        # ----------------------------------------------------
        # 1. GST Verification (Category 1: GST Portal)
        # ----------------------------------------------------
        if target_gstin:
            add_task_if_not_scheduled(
                task_type="GST_VERIFICATION",
                target=target_gstin,
                objective=f"Verify GSTIN {target_gstin} and retrieve registration status and details.",
                required_fields=["legal_name", "gst_status", "registered_address", "business_activity"],
                priority=1,
                preferred_sources=gst_pref,
                fallback_sources=gst_fall
            )
        elif primary_name:
            target_str = primary_name
            add_task_if_not_scheduled(
                task_type="GST_VERIFICATION",
                target=target_str,
                objective=f"Search GST portal and public records to identify taxpayer details for {primary_name}.",
                required_fields=["legal_name", "gst_status", "registered_address", "business_activity"],
                priority=1,
                preferred_sources=gst_pref,
                fallback_sources=gst_fall
            )

        # ----------------------------------------------------
        # 2. MCA Verification / Discovery (Category 2: MCA)
        # ----------------------------------------------------
        if target_cin:
            add_task_if_not_scheduled(
                task_type="MCA_VERIFICATION",
                target=target_cin,
                objective=f"Verify CIN {target_cin} and retrieve company registration details.",
                required_fields=["legal_name", "company_status", "incorporation_date", "registered_address"],
                priority=1,
                preferred_sources=mca_pref,
                fallback_sources=mca_fall
            )
        elif primary_name:
            target_mca = f"{primary_name} MCA company registration"
            add_task_if_not_scheduled(
                task_type="MCA_VERIFICATION",
                target=target_mca,
                objective=f"Search MCA corporate registry to verify incorporation and registration for {primary_name}.",
                required_fields=["legal_name", "company_status", "incorporation_date", "registered_address"],
                priority=1,
                preferred_sources=mca_pref,
                fallback_sources=mca_fall
            )

        # ----------------------------------------------------
        # 3. EPFO Verification / Discovery (Category 3: EPFO)
        # ----------------------------------------------------
        if target_epfo:
            add_task_if_not_scheduled(
                task_type="EPFO_VERIFICATION",
                target=target_epfo,
                objective=f"Verify EPFO code {target_epfo} and retrieve establishment registration details.",
                required_fields=["establishment_name", "epfo_status", "registered_address"],
                priority=1,
                preferred_sources=epfo_pref,
                fallback_sources=epfo_fall
            )
        elif primary_name:
            target_epfo_search = f"{primary_name} EPFO establishment"
            add_task_if_not_scheduled(
                task_type="EPFO_VERIFICATION",
                target=target_epfo_search,
                objective=f"Search EPFO establishment records to verify social security and employment registration for {primary_name}.",
                required_fields=["establishment_name", "epfo_status", "registered_address"],
                priority=1,
                preferred_sources=epfo_pref,
                fallback_sources=epfo_fall
            )

        # ----------------------------------------------------
        # 4. Website Verification / Discovery (Category 4: Company Website)
        # ----------------------------------------------------
        if target_website:
            add_task_if_not_scheduled(
                task_type="WEBSITE_VERIFICATION",
                target=target_website,
                objective=f"Analyze company website {target_website} to verify business claims and contact details.",
                required_fields=["website_status", "contact_address", "established_year"],
                priority=2,
                preferred_sources=web_pref,
                fallback_sources=web_fall
            )
        elif primary_name:
            target_web_search = f"{primary_name} official website"
            add_task_if_not_scheduled(
                task_type="WEBSITE_VERIFICATION",
                target=target_web_search,
                objective=f"Search and analyze official website for {primary_name} to verify operational claims and contact details.",
                required_fields=["website_status", "contact_address", "established_year"],
                priority=2,
                preferred_sources=web_pref,
                fallback_sources=web_fall
            )

        # ----------------------------------------------------
        # 5. Third-Party Research (Category 5: Third-Party Databases)
        # ----------------------------------------------------
        if primary_name:
            identifiers = [x for x in [primary_name, target_cin, target_gstin] if x]
            target_third_party = " ".join(identifiers)
            for src_meta in tp_directory_sources:
                add_task_if_not_scheduled(
                    task_type="THIRD_PARTY_RESEARCH",
                    target=target_third_party,
                    objective=f"Search third-party business database ({src_meta.display_name}) for {target_third_party}.",
                    required_fields=["legal_name", "company_status", "registered_address", "business_activity"],
                    priority=2,
                    preferred_sources=[src_meta.name],
                    fallback_sources=[],
                )

        # ----------------------------------------------------
        # 6. General Web Research / Entity Discovery (Category 6: General Web Search)
        # ----------------------------------------------------
        if primary_name:
            target_gen_web = f"{primary_name} in {location}".strip() if (primary_name and location) else primary_name
            if not target_gstin and not target_cin and not target_epfo:
                add_task_if_not_scheduled(
                    task_type="ENTITY_DISCOVERY",
                    target=target_gen_web,
                    objective=f"Search public records to discover matching legal entities, GSTIN, and CIN for {target_gen_web}.",
                    required_fields=["candidate_entities"],
                    priority=1,
                    preferred_sources=disc_pref,
                    fallback_sources=disc_fall
                )
            else:
                add_task_if_not_scheduled(
                    task_type="GENERAL_WEB_RESEARCH",
                    target=target_gen_web,
                    objective=f"Perform general web research to identify public footprint, news, and operating claims for {target_gen_web}.",
                    required_fields=["candidate_entities", "legal_name"],
                    priority=2,
                    preferred_sources=disc_pref,
                    fallback_sources=disc_fall
                )

        return new_tasks
