from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_user_service
from app.schemas.common import Page, Pagination
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def create_user(
    payload: UserCreate,
    service: UserService = Depends(get_user_service),
) -> UserOut:
    return UserOut.model_validate(await service.create_user(payload))


@router.get("", response_model=Page[UserOut])
async def list_users(
    service: UserService = Depends(get_user_service),
    pagination: Pagination = Depends(),
    is_active: bool | None = Query(default=None),
    email_icontains: str | None = Query(default=None, max_length=255),
) -> Page[UserOut]:
    items, total = await service.list_users(
        is_active=is_active,
        email_icontains=email_icontains,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[UserOut.model_validate(u) for u in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    service: UserService = Depends(get_user_service),
) -> UserOut:
    return UserOut.model_validate(await service.get_user(user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    service: UserService = Depends(get_user_service),
) -> UserOut:
    return UserOut.model_validate(await service.update_user(user_id, payload))
