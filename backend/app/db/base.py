from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models here so that they are registered on the Base metadata
# before any mapper configurations or queries are run.
from app.models.investigation import Investigation  # noqa: F401
from app.models.evidence import Evidence  # noqa: F401
from app.models.risk_signal import RiskSignal  # noqa: F401
from app.models.report import Report  # noqa: F401
from app.models.investigation_event import InvestigationEvent  # noqa: F401
from app.models.research_task import ResearchTask  # noqa: F401
from app.models.entity import Entity  # noqa: F401
from app.models.candidate_entity import CandidateEntity  # noqa: F401
from app.models.browser_session import BrowserSession  # noqa: F401
from app.models.browser_artifact import BrowserArtifact  # noqa: F401
from app.models.source_registry import SourceRegistry  # noqa: F401
