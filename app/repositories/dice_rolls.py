from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import DiceRoll


class DiceRollsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, roll_id: int) -> DiceRoll | None:
        return await self.session.get(DiceRoll, roll_id)

    async def get_last_by_user(self, user_id: int) -> DiceRoll | None:
        return await self.session.scalar(
            select(DiceRoll).where(DiceRoll.user_id == user_id).order_by(DiceRoll.created_at.desc()).limit(1)
        )

    async def get_valid_discount(self, user_id: int, code: str, now: datetime) -> DiceRoll | None:
        return await self.session.scalar(
            select(DiceRoll).where(
                DiceRoll.user_id == user_id,
                DiceRoll.discount_code == code,
                DiceRoll.won.is_(True),
                DiceRoll.used.is_(False),
                (DiceRoll.expires_at.is_(None)) | (DiceRoll.expires_at > now),
            )
        )

    async def get_by_discount_code(self, code: str) -> DiceRoll | None:
        return await self.session.scalar(select(DiceRoll).where(DiceRoll.discount_code == code))

    async def list_recent_winners(self, limit: int = 10) -> list[DiceRoll]:
        result = await self.session.scalars(
            select(DiceRoll)
            .options(joinedload(DiceRoll.user))
            .where(DiceRoll.won.is_(True))
            .order_by(DiceRoll.created_at.desc())
            .limit(limit)
        )
        return list(result.unique().all())

    async def create(
        self,
        *,
        user_id: int,
        dice_value: int,
        won: bool,
        discount_percent: int = 0,
        discount_code: str | None = None,
        expires_at: datetime | None = None,
    ) -> DiceRoll:
        roll = DiceRoll(
            user_id=user_id,
            dice_value=dice_value,
            won=won,
            discount_percent=discount_percent,
            discount_code=discount_code,
            expires_at=expires_at,
        )
        self.session.add(roll)
        await self.session.flush()
        return roll
