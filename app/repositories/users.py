from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.utils.tracking import generate_referral_code


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_by_referral_code(self, referral_code: str) -> User | None:
        return await self.session.scalar(select(User).where(User.referral_code == referral_code))

    async def count_referrals(self, referrer_id: int) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(User).where(User.referred_by_id == referrer_id))
            or 0
        )

    async def list_admin_telegram_ids(self) -> list[int]:
        result = await self.session.scalars(select(User.telegram_id).where(User.is_admin.is_(True)))
        return list(result.all())

    async def count_all(self) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(User)) or 0)

    async def count_phone_verified(self) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(User).where(User.is_phone_verified.is_(True)))
            or 0
        )

    async def list_recent(self, limit: int = 10) -> list[User]:
        result = await self.session.scalars(select(User).order_by(User.created_at.desc()).limit(limit))
        return list(result.all())

    async def search(self, query: str, limit: int = 10) -> list[User]:
        normalized = query.strip().removeprefix("@")
        conditions = [
            User.telegram_username.ilike(f"%{normalized}%"),
            User.phone_number.ilike(f"%{normalized}%"),
            User.first_name.ilike(f"%{normalized}%"),
        ]
        if normalized.isdigit():
            conditions.append(User.telegram_id == int(normalized))
        result = await self.session.scalars(select(User).where(or_(*conditions)).limit(limit))
        return list(result.all())

    async def verify_phone(self, user: User, phone_number: str, verified_at: datetime) -> User:
        user.phone_number = phone_number
        user.is_phone_verified = True
        user.verified_at = verified_at
        await self.session.flush()
        return user

    async def create_or_update_from_telegram(
        self,
        *,
        telegram_id: int,
        telegram_username: str | None,
        first_name: str | None,
        is_admin: bool,
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(
                telegram_id=telegram_id,
                telegram_username=telegram_username,
                first_name=first_name,
                referral_code=generate_referral_code(telegram_id),
                is_admin=is_admin,
            )
            self.session.add(user)
        else:
            user.telegram_username = telegram_username
            user.first_name = first_name
            user.is_admin = bool(user.is_admin or is_admin)
            if not user.referral_code:
                user.referral_code = generate_referral_code(telegram_id)

        await self.session.flush()
        return user
