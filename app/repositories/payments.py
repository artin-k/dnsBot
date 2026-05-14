from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Order, Payment, PaymentStatus


class PaymentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, payment_id: int) -> Payment | None:
        return await self.session.get(Payment, payment_id)

    async def get_with_details(self, payment_id: int) -> Payment | None:
        return await self.session.scalar(
            select(Payment)
            .options(
                joinedload(Payment.user),
                joinedload(Payment.order).joinedload(Order.plan),
                joinedload(Payment.order).joinedload(Order.renewal_service),
                joinedload(Payment.order).joinedload(Order.config_inventory_item),
            )
            .where(Payment.id == payment_id)
        )

    async def get_by_order_id(self, order_id: int) -> Payment | None:
        return await self.session.scalar(select(Payment).where(Payment.order_id == order_id))

    async def list_pending_review(self) -> list[Payment]:
        result = await self.session.scalars(
            select(Payment)
            .options(
                joinedload(Payment.user),
                joinedload(Payment.order).joinedload(Order.plan),
                joinedload(Payment.order).joinedload(Order.renewal_service),
                joinedload(Payment.order).joinedload(Order.config_inventory_item),
            )
            .where(
                Payment.order_id.is_not(None),
                Payment.status == PaymentStatus.PENDING.value,
                Payment.receipt_file_id.is_not(None),
            )
            .order_by(Payment.created_at.asc())
        )
        return list(result.unique().all())

    async def list_user_pending_without_receipt(self, user_id: int) -> list[Payment]:
        result = await self.session.scalars(
            select(Payment)
            .options(
                joinedload(Payment.order).joinedload(Order.plan),
                joinedload(Payment.order).joinedload(Order.config_inventory_item),
            )
            .where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PENDING.value,
                Payment.receipt_file_id.is_(None),
            )
            .order_by(Payment.created_at.desc())
        )
        return list(result.unique().all())

    async def create(
        self,
        *,
        order_id: int | None,
        user_id: int,
        amount: int,
        method: str = "manual",
        status: str = PaymentStatus.PENDING.value,
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            user_id=user_id,
            amount=amount,
            method=method,
            status=status,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment
