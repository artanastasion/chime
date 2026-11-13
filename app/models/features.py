"""Translate feature domain entities."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

if TYPE_CHECKING:
    from app.models.track import Track
    from app.models.user import User


class AudioAnalysisJobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TrackFeature(Base):
    __tablename__ = "track_features"

    track_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracks.track_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    feature_version: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    energy: Mapped[float | None] = mapped_column(Float)
    valence: Mapped[float | None] = mapped_column(Float)
    tempo: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="ready",
        server_default="ready",
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    track: Mapped[Track] = relationship("Track", back_populates="features")


class AudioAnalysisJob(Base):
    __tablename__ = "audio_analysis_jobs"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "feature_version", name="uq_audio_analysis_jobs_asset_version"
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[AudioAnalysisJobStatus] = mapped_column(
        SAEnum(
            AudioAnalysisJobStatus,
            name="audio_analysis_job_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=AudioAnalysisJobStatus.QUEUED,
        server_default=AudioAnalysisJobStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserTastePreference(Base):
    __tablename__ = "user_taste_preferences"
    __table_args__ = (
        CheckConstraint("weight >= 0 AND weight <= 1", name="weight_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    genre_code: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="taste_preferences")


class TranslationLink(Base):
    __tablename__ = "translation_links"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_track_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracks.track_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sender: Mapped[User] = relationship("User", back_populates="sent_translation_links")
    source_track: Mapped[Track] = relationship("Track", back_populates="translation_links")
    results: Mapped[list[TranslationResult]] = relationship(
        "TranslationResult",
        back_populates="link",
        cascade="all, delete-orphan",
    )


class TranslationResult(Base):
    __tablename__ = "translation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("translation_links.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_track_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracks.track_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    feature_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    link: Mapped[TranslationLink] = relationship("TranslationLink", back_populates="results")
    recipient: Mapped[User] = relationship("User", back_populates="translation_results")
    target_track: Mapped[Track] = relationship("Track", back_populates="translation_results")
