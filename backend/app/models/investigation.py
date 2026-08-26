import uuid
from datetime import datetime
from typing import List

from sqlalchemy import DateTime, String, Text, func
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

    risk_signals: Mapped[List["RiskSignal"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
    )
