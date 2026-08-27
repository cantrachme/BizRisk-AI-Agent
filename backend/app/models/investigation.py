import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, String, Text, func, Integer, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(
        String(50),
        default="created",
        nullable=False,
    )
    input_data: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_graph_node: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    completed_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    persistent_graph_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_node: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    resolved_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    evidences: Mapped[List["Evidence"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    research_tasks: Mapped[List["ResearchTask"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    reports: Mapped[List["Report"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    events: Mapped[List["InvestigationEvent"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    risk_signals: Mapped[List["RiskSignal"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    resolved_entity = relationship("Entity", foreign_keys=[resolved_entity_id])

    candidate_entities: Mapped[List["CandidateEntity"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    browser_sessions: Mapped[List["BrowserSession"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )

    browser_artifacts: Mapped[List["BrowserArtifact"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )
