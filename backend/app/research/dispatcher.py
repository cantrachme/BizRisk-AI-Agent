from __future__ import annotations

import concurrent.futures
import uuid
from typing import Callable, Dict, List, Optional

from app.graph.state import ResearchResult, ResearchTask
from app.research.base import BaseResearchProvider
from app.research.company_website import CompanyWebsiteResearchProvider
from app.research.epfo import EpfoResearchProvider
from app.research.generic_web import GenericWebResearchProvider
from app.research.gst import GstResearchProvider
from app.research.mca import McaResearchProvider


class ResearchDispatcher:
    """
    Central dispatcher that resolves tasks to specialized research providers
    and coordinates concurrent execution of independent research tasks.
    """

    def __init__(self, fetcher: Optional[Callable[[str], str]] = None):
        self.fetcher = fetcher
        self._providers: List[BaseResearchProvider] = [
            GstResearchProvider(fetcher=fetcher),
            McaResearchProvider(fetcher=fetcher),
            EpfoResearchProvider(fetcher=fetcher),
            CompanyWebsiteResearchProvider(fetcher=fetcher),
            GenericWebResearchProvider(fetcher=fetcher),
        ]

    def register_provider(self, provider: BaseResearchProvider):
        self._providers.insert(0, provider)

    def get_provider_for_task(self, task: ResearchTask) -> Optional[BaseResearchProvider]:
        for provider in self._providers:
            if provider.can_handle(task):
                return provider
        return None

    def execute_task(
        self,
        task: ResearchTask,
        investigation_id: Optional[uuid.UUID] = None,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> List[ResearchResult]:
        provider = self.get_provider_for_task(task)
        if not provider:
            return []

        # If a custom fetcher was passed for this execution (e.g. test mock), apply it
        if fetcher is not None:
            provider.fetcher = fetcher

        try:
            return provider.research(task, investigation_id=investigation_id)
        except Exception as ex:
            print(f"[DIAGNOSTIC] Provider {provider.provider_name} failed on task {task.task_id}: {ex}", flush=True)
            return []

    def execute_tasks_concurrent(
        self,
        tasks: List[ResearchTask],
        investigation_id: Optional[uuid.UUID] = None,
        max_workers: int = 5,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> Dict[str, List[ResearchResult]]:
        if not tasks:
            return {}

        results_by_task: Dict[str, List[ResearchResult]] = {}
        worker_count = min(len(tasks), max_workers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_task = {
                executor.submit(self.execute_task, task, investigation_id, fetcher): task
                for task in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    task_results = future.result()
                    results_by_task[task.task_id] = task_results
                except Exception as ex:
                    print(f"[DIAGNOSTIC] Task execution error for {task.task_id}: {ex}", flush=True)
                    results_by_task[task.task_id] = []

        return results_by_task

    def dispatch_tasks(
        self,
        tasks: List[ResearchTask],
        investigation_id: Optional[uuid.UUID] = None,
        max_workers: int = 5,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> List[ResearchResult]:
        """Convenience method returning a flat list of results from all dispatched tasks."""
        results_by_task = self.execute_tasks_concurrent(
            tasks,
            investigation_id=investigation_id,
            max_workers=max_workers,
            fetcher=fetcher,
        )
        all_results: List[ResearchResult] = []
        for task_results in results_by_task.values():
            all_results.extend(task_results)
        return all_results


default_dispatcher = ResearchDispatcher()
