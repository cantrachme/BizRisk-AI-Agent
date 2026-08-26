from datetime import datetime, timezone

from app.graph.state import ResearchResult, ResearchTask


SOURCES = {
    "gst.gov.in": ("GST Portal", "https://www.gst.gov.in", 0.95),
    "mca.gov.in": ("MCA Portal", "https://www.mca.gov.in", 0.95),
    "company_website": ("Company Website", None, 0.85),
    "generic_web": ("General Web", None, 0.60),
    "third_party": ("Third-Party Source", None, 0.50),
}


class BrowserResearchAgent:
    def execute(self, task: ResearchTask) -> list[ResearchResult]:
        source = self._select_source(task)

        if source is None:
            return []

        if task.task_type not in {
            "ENTITY_DISCOVERY",
            "GST_VERIFICATION",
            "MCA_VERIFICATION",
            "WEBSITE_VERIFICATION",
            "GENERAL_WEB_RESEARCH",
        }:
            return []

        source_name, source_url, confidence = SOURCES[source]

        return [
            ResearchResult(
                result_id=f"RESULT-{task.task_id}-{index:03d}",
                task_id=task.task_id,
                field_name=field_name,
                field_value=self._placeholder_value(task, field_name),
                source_name=source_name,
                source_url=source_url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                confidence=confidence,
            )
            for index, field_name in enumerate(task.required_fields, start=1)
        ]

    @staticmethod
    def _select_source(task: ResearchTask) -> str | None:
        for source in [*task.preferred_sources, *task.fallback_sources]:
            if source in SOURCES:
                return source

        return None

    @staticmethod
    def _placeholder_value(task: ResearchTask, field_name: str):
        if field_name == "candidate_entities":
            return [
                {
                    "name": task.target,
                    "confidence": 0.0,
                }
            ]

        return None
