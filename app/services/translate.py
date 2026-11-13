from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track, User
from app.models.features import (
    AudioAnalysisJob,
    TrackFeature,
    TranslationLink,
    TranslationResult,
    UserTastePreference,
)
from app.services.errors import (
    CatalogEntityNotFoundError,
    TranslationResultConflictError,
    TranslationTokenError,
)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TranslateService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_taste_preference(
        self, user_id: uuid.UUID, genre_code: str, weight: float
    ) -> UserTastePreference:
        user = await self._session.get(User, user_id)
        if user is None:
            raise CatalogEntityNotFoundError(f"user {user_id} not found")

        normalized = genre_code.lower()
        pref = await self._session.scalar(
            select(UserTastePreference).where(
                UserTastePreference.user_id == user_id,
                UserTastePreference.genre_code == normalized,
            )
        )
        if pref is not None:
            pref.weight = weight
            await self._session.commit()
            await self._session.refresh(pref)
            return pref

        pref = UserTastePreference(
            user_id=user_id,
            genre_code=normalized,
            weight=weight,
        )
        self._session.add(pref)
        await self._session.commit()
        await self._session.refresh(pref)
        return pref

    async def list_taste_preferences(self, user_id: uuid.UUID) -> list[UserTastePreference]:
        await self.get_user(user_id)
        return (
            await self._session.execute(
                select(UserTastePreference)
                .where(UserTastePreference.user_id == user_id)
                .order_by(UserTastePreference.genre_code)
            )
        ).scalars().all()

    async def create_translation_link(
        self,
        *,
        sender_id: uuid.UUID,
        source_track_id: uuid.UUID,
        token: str | None = None,
        expires_in_hours: int = 24,
    ) -> tuple[TranslationLink, str]:
        await self.get_user(sender_id)
        source_track = await self._session.get(Track, source_track_id)
        if source_track is None:
            raise CatalogEntityNotFoundError(f"track {source_track_id} not found")

        generated_token = token or secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        link = TranslationLink(
            sender_id=sender_id,
            source_track_id=source_track_id,
            token_hash=_hash_token(generated_token),
            expires_at=expires_at,
        )
        self._session.add(link)
        await self._session.commit()
        await self._session.refresh(link)
        return link, generated_token

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise CatalogEntityNotFoundError(f"user {user_id} not found")
        return user

    async def get_translation_link(self, link_id: uuid.UUID) -> TranslationLink:
        link = await self._session.get(TranslationLink, link_id)
        if link is None:
            raise CatalogEntityNotFoundError(f"translation link {link_id} not found")
        return link

    async def get_translation_link_by_token(self, token: str) -> TranslationLink:
        token_hash = _hash_token(token)
        link = await self._session.scalar(
            select(TranslationLink).where(TranslationLink.token_hash == token_hash)
        )
        if link is None:
            raise TranslationTokenError("invalid translation link token")
        if link.revoked_at is not None:
            raise TranslationTokenError("translation link has been revoked")
        expires_at = link.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise TranslationTokenError("translation link has expired")
        return link

    async def create_track_feature(
        self,
        *,
        track_id: uuid.UUID,
        feature_version: str,
        embedding: dict[str, float] | None = None,
        energy: float | None = None,
        valence: float | None = None,
        tempo: float | None = None,
        status: str = "ready",
    ) -> TrackFeature:
        track = await self._session.get(Track, track_id)
        if track is None:
            raise CatalogEntityNotFoundError(f"track {track_id} not found")
        feature = await self._session.get(TrackFeature, track_id)
        if feature is None:
            feature = TrackFeature(track_id=track_id)
            self._session.add(feature)
        feature.feature_version = feature_version
        feature.embedding = embedding
        feature.energy = energy
        feature.valence = valence
        feature.tempo = tempo
        feature.status = status
        feature.ready_at = datetime.now(timezone.utc)
        await self._session.commit()
        await self._session.refresh(feature)
        return feature

    async def create_result(
        self,
        *,
        link_id: uuid.UUID,
        recipient_id: uuid.UUID,
        target_track_id: uuid.UUID,
        score: float,
        feature_version: str,
    ) -> TranslationResult:
        link = await self.get_translation_link(link_id)
        await self.get_user(recipient_id)
        target_track = await self._session.get(Track, target_track_id)
        if target_track is None:
            raise CatalogEntityNotFoundError(f"track {target_track_id} not found")

        if link.source_track_id == target_track_id:
            raise TranslationResultConflictError("target track cannot be the source track")

        result = TranslationResult(
            link_id=link.id,
            recipient_id=recipient_id,
            target_track_id=target_track_id,
            score=score,
            feature_version=feature_version,
        )
        self._session.add(result)
        await self._session.commit()
        await self._session.refresh(result)
        return result

    async def create_analysis_job(
        self,
        *,
        asset_id: uuid.UUID,
        feature_version: str,
    ) -> AudioAnalysisJob:
        job = AudioAnalysisJob(
            asset_id=asset_id,
            feature_version=feature_version,
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def list_translation_results(self, link_id: uuid.UUID) -> list[TranslationResult]:
        await self.get_translation_link(link_id)
        return (
            await self._session.execute(
                select(TranslationResult)
                .where(TranslationResult.link_id == link_id)
                .order_by(TranslationResult.created_at.desc())
            )
        ).scalars().all()
