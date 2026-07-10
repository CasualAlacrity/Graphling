import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class MemoryClassification(str, enum.Enum):
    CORRECTION = "correction"
    BEHAVIORAL_INSTRUCTION = "behavioral_instruction"
    PILOT_PREFERENCE = "pilot_preference"
    INCIDENTAL_DETAIL = "incidental_detail"
    EPISODIC_EVENT = "episodic_event"


# Extraction only ever classifies — it never guesses a weight. Small models are
# reliable at categorizing, not at judging importance, so weight is a fixed
# lookup by classification instead of LLM output.
DEFAULT_WEIGHTS: dict[MemoryClassification, float] = {
    MemoryClassification.CORRECTION: 1.0,
    MemoryClassification.BEHAVIORAL_INSTRUCTION: 0.95,
    MemoryClassification.PILOT_PREFERENCE: 0.6,
    MemoryClassification.INCIDENTAL_DETAIL: 0.3,
    MemoryClassification.EPISODIC_EVENT: 0.5,
}


class PilotMemory(Base):
    __tablename__ = "pilot_memory"

    content: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[MemoryClassification] = mapped_column(
        Enum(MemoryClassification, name="memory_classification"),
        nullable=False,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    last_reinforced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
