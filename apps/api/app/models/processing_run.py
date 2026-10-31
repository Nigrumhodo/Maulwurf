"""Durable `processing_runs` ledger (A2.1/A2.20, S3.4 versioning).

One row identifies one stage execution for a class: the transcript version it reads and
the configuration/prompt version it applies. The unique key
`(audio_id, stage, transcript_version, config_version)` makes redeliveries and retries
idempotent, while a version bump creates a NEW row instead of overwriting the previous one,
so old runs stay queryable.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, uuid_pk


class ProcessingRun(TenantOwned, Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        UniqueConstraint("audio_id", "stage", "transcript_version", "config_version"),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        CheckConstraint("stage IN ('index', 'analyze')", name="stage"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')", name="status"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # Monotonic per run: every successful claim takes a new value, so a worker from an
    # earlier claim cannot complete or activate the work of the current one.
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
