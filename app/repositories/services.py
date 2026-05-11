from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import VPNService, VPNServiceStatus


class ServicesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        order_id: int,
        plan_id: int,
        username: str,
        config_link: str,
        subscription_link: str,
        volume_gb: int,
        duration_days: int,
        expire_at,
        status: str = VPNServiceStatus.ACTIVE.value,
    ) -> VPNService:
        service = VPNService(
            user_id=user_id,
            order_id=order_id,
            plan_id=plan_id,
            username=username,
            config_link=config_link,
            subscription_link=subscription_link,
            volume_gb=volume_gb,
            duration_days=duration_days,
            expire_at=expire_at,
            status=status,
        )
        self.session.add(service)
        await self.session.flush()
        return service

    async def list_active_by_user(self, user_id: int) -> list[VPNService]:
        result = await self.session.scalars(
            select(VPNService)
            .options(joinedload(VPNService.plan))
            .where(
                VPNService.user_id == user_id,
                VPNService.status == VPNServiceStatus.ACTIVE.value,
            )
            .order_by(VPNService.expire_at.desc())
        )
        return list(result.unique().all())

    async def list_by_user(self, user_id: int) -> list[VPNService]:
        active_first = case((VPNService.status == VPNServiceStatus.ACTIVE.value, 0), else_=1)
        result = await self.session.scalars(
            select(VPNService)
            .options(joinedload(VPNService.plan))
            .where(VPNService.user_id == user_id)
            .order_by(active_first.asc(), VPNService.expire_at.desc())
        )
        return list(result.unique().all())

    async def get_user_service(self, service_id: int, user_id: int) -> VPNService | None:
        return await self.session.scalar(
            select(VPNService)
            .options(joinedload(VPNService.plan))
            .where(VPNService.id == service_id, VPNService.user_id == user_id)
        )

    async def get(self, service_id: int) -> VPNService | None:
        return await self.session.scalar(
            select(VPNService)
            .options(joinedload(VPNService.plan), joinedload(VPNService.user))
            .where(VPNService.id == service_id)
        )

    async def list_recent(self, limit: int = 10) -> list[VPNService]:
        result = await self.session.scalars(
            select(VPNService)
            .options(joinedload(VPNService.plan), joinedload(VPNService.user))
            .order_by(VPNService.created_at.desc())
            .limit(limit)
        )
        return list(result.unique().all())

    async def search(self, query: str, limit: int = 10) -> list[VPNService]:
        normalized = query.strip().removeprefix("@")
        conditions = [VPNService.username.ilike(f"%{normalized}%")]
        if normalized.isdigit():
            conditions.append(VPNService.user.has(telegram_id=int(normalized)))
        result = await self.session.scalars(
            select(VPNService)
            .options(joinedload(VPNService.plan), joinedload(VPNService.user))
            .where(or_(*conditions))
            .limit(limit)
        )
        return list(result.unique().all())

    async def count_by_user(self, user_id: int) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(VPNService).where(VPNService.user_id == user_id))
            or 0
        )
