"""Platform user account."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.features import TranslationLink, TranslationResult, UserTastePreference


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Authentication is intentionally out of scope for now; no password
    # columns are stored until an auth flow is actually implemented.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    taste_preferences: Mapped[list[UserTastePreference]] = relationship(
        "UserTastePreference",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    sent_translation_links: Mapped[list[TranslationLink]] = relationship(
        "TranslationLink",
        back_populates="sender",
        cascade="all, delete-orphan",
    )
    translation_results: Mapped[list[TranslationResult]] = relationship(
        "TranslationResult",
        back_populates="recipient",
        cascade="all, delete-orphan",
    )
