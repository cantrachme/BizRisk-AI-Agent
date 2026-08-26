from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models here so that they are registered on the Base metadata
# before any mapper configurations or queries are run.
from app.models.investigation import Investigation  # noqa: F401
from app.models.evidence import Evidence  # noqa: F401
from app.models.risk_signal import RiskSignal  # noqa: F401
