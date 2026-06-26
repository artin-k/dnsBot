# app/repositories/plans.py
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Plan, VPNService


class PlansRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, plan_id: int) -> Plan | None:
        return await self.session.get(Plan, plan_id)

    async def list_active(self) -> list[Plan]:
        result = await self.session.scalars(
            select(Plan)
            .where(Plan.is_active.is_(True))
            .order_by(Plan.sort_order.asc(), Plan.price.asc(), Plan.id.asc())
        )
        return list(result.all())

    async def list_all(self) -> list[Plan]:
        result = await self.session.scalars(select(Plan).order_by(Plan.sort_order.asc(), Plan.id.asc()))
        return list(result.all())

    async def create(
        self,
        *,
        title: str,
        duration_hours: int,  # Fixed: Changed from duration_days to duration_hours
        volume_gb: int,
        price: int,
        description: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> Plan:
        plan = Plan(
            title=title,
            duration_hours=duration_hours,  # Fixed: Mapped to duration_hours on the SQLAlchemy model
            volume_gb=volume_gb,
            price=price,
            description=description,
            sort_order=sort_order,
            is_active=is_active,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def set_active(self, plan_id: int, is_active: bool) -> Plan | None:
        plan = await self.get(plan_id)
        if plan is None:
            return None
        plan.is_active = is_active
        await self.session.flush()
        return plan

    async def update_fields(self, plan_id: int, **fields) -> Plan | None:
        plan = await self.get(plan_id)
        if plan is None:
            return None
        for key, value in fields.items():
            setattr(plan, key, value)
        await self.session.flush()
        return plan

    async def has_usage(self, plan_id: int) -> bool:
        orders_count = await self.session.scalar(select(func.count()).select_from(Order).where(Order.plan_id == plan_id))
        services_count = await self.session.scalar(
            select(func.count()).select_from(VPNService).where(VPNService.plan_id == plan_id)
        )
        return bool((orders_count or 0) > 0 or (services_count or 0) > 0)

    async def delete(self, plan_id: int) -> bool:
        plan = await self.get(plan_id)
        if plan is None:
            return False
        await self.session.delete(plan)
        await self.session.flush()
        return True