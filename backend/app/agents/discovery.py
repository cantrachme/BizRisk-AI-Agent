from typing import Any


class DiscoveryAgent:
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

        return {
            "candidate_entities": [
                {
                    "name": business_name,
                    "gstin": gstin,
                    "cin": cin,
                    "website": website,
                    "location": location,
                    "confidence": confidence,
                }
            ]
        }
