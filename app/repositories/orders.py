from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Order, OrderKind


class OrdersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_with_details(self, order_id: int) -> Order | None:
        return await self.session.scalar(
            select(Order)
            .options(
                joinedload(Order.user),
                joinedload(Order.plan),
                joinedload(Order.payment),
                joinedload(Order.renewal_service),
            )
            .where(Order.id == order_id)
        )

    async def get_by_tracking_code(self, tracking_code: str) -> Order | None:
        return await self.session.scalar(select(Order).where(Order.tracking_code == tracking_code))

    async def get_by_tracking_code_for_user(self, tracking_code: str, user_id: int) -> Order | None:
        return await self.session.scalar(
            select(Order)
            .options(
                joinedload(Order.plan),
                joinedload(Order.payment),
                joinedload(Order.renewal_service),
            )
            .where(Order.tracking_code == tracking_code, Order.user_id == user_id)
        )

    async def list_by_user(self, user_id: int) -> list[Order]:
        result = await self.session.scalars(
            select(Order)
            .options(
                joinedload(Order.plan),
                joinedload(Order.payment),
                joinedload(Order.renewal_service),
            )
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        return list(result.unique().all())

    async def count_by_user(self, user_id: int) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user_id)) or 0)

    async def create(
        self,
        *,
        user_id: int,
        plan_id: int,
        custom_username: str | None,
        order_kind: str = OrderKind.PURCHASE.value,
        service_id: int | None = None,
        tracking_code: str,
        amount: int,
        discount_code: str | None = None,
        discount_percent: int = 0,
        discount_amount: int = 0,
        status: str,
        expires_at,
    ) -> Order:
        order = Order(
            user_id=user_id,
            plan_id=plan_id,
            custom_username=custom_username,
            order_kind=order_kind,
            service_id=service_id,
            tracking_code=tracking_code,
            amount=amount,
            discount_code=discount_code,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            status=status,
            expires_at=expires_at,
        )
        self.session.add(order)
        await self.session.flush()
        return order
