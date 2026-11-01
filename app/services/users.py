from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas.user import UserCreate, UserUpdate
from app.services.catalog import _icontains
from app.services.errors import CatalogEntityNotFoundError


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _commit(self, user: User) -> User:
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(user)
        return user

    async def create_user(self, data: UserCreate) -> User:
        user = User(email=data.email, is_active=data.is_active)
        self._session.add(user)
        return await self._commit(user)

    async def list_users(
        self,
        *,
        is_active: bool | None = None,
        email_icontains: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        stmt = select(User)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if email_icontains:
            stmt = stmt.where(
                User.email.ilike(_icontains(email_icontains), escape="\\")
            )
        total = await self._session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        stmt = stmt.order_by(User.created_at.desc(), User.id)
        items = (
            await self._session.execute(stmt.limit(limit).offset(offset))
        ).scalars()
        return list(items), total or 0

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise CatalogEntityNotFoundError(f"user {user_id} not found")
        return user

    async def update_user(self, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = await self.get_user(user_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        return await self._commit(user)
