from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.api.deps import get_session
from app.schemas.translate import (
    TastePreferenceCreate,
    TastePreferenceOut,
    TrackFeatureCreate,
    TrackFeatureOut,
    TranslationLinkCreate,
    TranslationLinkOut,
    TranslationResultCreate,
    TranslationResultOut,
)
from app.services.translate import TranslateService

router = APIRouter(tags=["Translate"])


def _translate_service(session=Depends(get_session)) -> TranslateService:
    return TranslateService(session)


@router.post("/translations", status_code=status.HTTP_201_CREATED, response_model=TranslationLinkOut)
async def create_translation_link(
    payload: TranslationLinkCreate,
    service: TranslateService = Depends(_translate_service),
) -> TranslationLinkOut:
    link, token = await service.create_translation_link(
        sender_id=payload.sender_id,
        source_track_id=payload.source_track_id,
        token=payload.token,
        expires_in_hours=payload.expires_in_hours,
    )
    return TranslationLinkOut(
        id=link.id,
        sender_id=link.sender_id,
        source_track_id=link.source_track_id,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        created_at=link.created_at,
        token=token,
    )


@router.get("/translations/{link_id}", response_model=TranslationLinkOut)
async def get_translation_link(
    link_id: uuid.UUID,
    service: TranslateService = Depends(_translate_service),
) -> TranslationLinkOut:
    link = await service.get_translation_link(link_id)
    return TranslationLinkOut.model_validate(link)


@router.post(
    "/users/{user_id}/taste-preferences",
    status_code=status.HTTP_201_CREATED,
    response_model=TastePreferenceOut,
)
async def create_taste_preference(
    user_id: uuid.UUID,
    payload: TastePreferenceCreate,
    service: TranslateService = Depends(_translate_service),
) -> TastePreferenceOut:
    if payload.user_id != user_id:
        raise ValueError("user_id in path and body must match")
    pref = await service.create_taste_preference(
        payload.user_id,
        payload.genre_code,
        payload.weight,
    )
    return TastePreferenceOut.model_validate(pref)


@router.get("/users/{user_id}/taste-preferences", response_model=list[TastePreferenceOut])
async def list_taste_preferences(
    user_id: uuid.UUID,
    service: TranslateService = Depends(_translate_service),
) -> list[TastePreferenceOut]:
    return [
        TastePreferenceOut.model_validate(p)
        for p in await service.list_taste_preferences(user_id)
    ]


@router.post("/track-features", status_code=status.HTTP_201_CREATED, response_model=TrackFeatureOut)
async def create_track_feature(
    payload: TrackFeatureCreate,
    service: TranslateService = Depends(_translate_service),
) -> TrackFeatureOut:
    feature = await service.create_track_feature(
        track_id=payload.track_id,
        feature_version=payload.feature_version,
        embedding=payload.embedding,
        energy=payload.energy,
        valence=payload.valence,
        tempo=payload.tempo,
        status=payload.status,
    )
    return TrackFeatureOut.model_validate(feature)


@router.post(
    "/translations/{link_id}/results",
    status_code=status.HTTP_201_CREATED,
    response_model=TranslationResultOut,
)
async def create_translation_result(
    link_id: uuid.UUID,
    payload: TranslationResultCreate,
    service: TranslateService = Depends(_translate_service),
) -> TranslationResultOut:
    if payload.link_id != link_id:
        raise ValueError("link_id in path and body must match")
    result = await service.create_result(
        link_id=payload.link_id,
        recipient_id=payload.recipient_id,
        target_track_id=payload.target_track_id,
        score=payload.score,
        feature_version=payload.feature_version,
    )
    return TranslationResultOut.model_validate(result)


@router.get("/t/{token}", response_model=TranslationLinkOut)
async def get_translation_link_by_token(
    token: str,
    service: TranslateService = Depends(_translate_service),
) -> TranslationLinkOut:
    link = await service.get_translation_link_by_token(token)
    return TranslationLinkOut.model_validate(link)


@router.post(
    "/t/{token}/resolve",
    status_code=status.HTTP_201_CREATED,
    response_model=TranslationResultOut,
)
async def resolve_translation_token(
    token: str,
    payload: TranslationResultCreate,
    service: TranslateService = Depends(_translate_service),
) -> TranslationResultOut:
    link = await service.get_translation_link_by_token(token)
    if payload.link_id != link.id:
        payload.link_id = link.id
    result = await service.create_result(
        link_id=link.id,
        recipient_id=payload.recipient_id,
        target_track_id=payload.target_track_id,
        score=payload.score,
        feature_version=payload.feature_version,
    )
    return TranslationResultOut.model_validate(result)
