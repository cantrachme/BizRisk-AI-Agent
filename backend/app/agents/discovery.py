from typing import Any


class DiscoveryAgent:
    def __init__(self, llm=None, prompt_version: str = "v1"):
        from app.core.llm import get_llm_provider
        from app.core.prompts import load_prompt
        self.llm = llm or get_llm_provider(temperature=0.0)
        self.prompt_version = prompt_version
        self.prompt = load_prompt("discovery", prompt_version)

    def process(self, investigation_input: dict[str, Any]) -> dict[str, Any]:
        business_name = investigation_input.get("business_name")
        gstin = investigation_input.get("gstin")
        cin = investigation_input.get("cin")
        website = investigation_input.get("website")
        location = investigation_input.get("location")

        if not any([business_name, gstin, cin, website, location]):
            return {"candidate_entities": []}

        confidence = 0.0

        if gstin or cin:
            confidence = 0.95
        elif business_name and website and location:
            confidence = 0.80
        elif business_name and (website or location):
            confidence = 0.70
        elif business_name:
            confidence = 0.50
        else:
            confidence = 0.30

        candidates = [
            {
                "name": business_name,
                "gstin": gstin,
                "cin": cin,
                "website": website,
                "location": location,
                "confidence": confidence,
            }
        ]

        self._augment_candidates_with_llm(investigation_input, candidates)

        return {"candidate_entities": candidates}

    def _augment_candidates_with_llm(
        self,
        investigation_input: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        """
        Optionally adds LLM-proposed candidate entities. No-op on the mock/test
        provider or on any LLM failure. LLM candidates are advisory only: the
        deterministic candidate above always remains, and LLM confidence is
        clamped so it can never present as a higher-authority match.
        """
        from app.core.llm import run_structured_sync
        from app.schemas.agent_outputs import DiscoveryOutput

        prompt = (
            f"{self.prompt}\n\n"
            "Given the following intake information about a business, list any additional "
            "plausible candidate legal entities (name, and GSTIN/CIN only if explicitly "
            "implied). Do not invent identifiers.\n\n"
            f"INTAKE: {investigation_input}\n"
        )
        llm_out = run_structured_sync(
            self.llm,
            prompt,
            DiscoveryOutput,
            system_instruction=(
                "You assist entity discovery for business due-diligence. Only return "
                "candidates supported by the intake text. Never fabricate identifiers."
            ),
        )
        if not llm_out or not getattr(llm_out, "candidate_entities", None):
            return

        seen = {
            str(c.get("name") or "").strip().lower()
            for c in candidates
            if c.get("name")
        }
        for cand in llm_out.candidate_entities:
            name = (getattr(cand, "business_name", "") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            raw_conf = float(getattr(cand, "confidence", 0.0) or 0.0)
            candidates.append(
                {
                    "name": name,
                    "gstin": getattr(cand, "gstin", None),
                    "cin": getattr(cand, "cin", None),
                    "website": None,
                    "location": None,
                    "confidence": max(0.0, min(raw_conf, 0.90)),
                    "source": "llm_discovery",
                }
            )
