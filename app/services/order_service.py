from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderKind, OrderStatus, PaymentStatus, Plan, User
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.utils.tracking import generate_tracking_code


class OrderService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.orders = OrdersRepository(session)
        self.payments = PaymentsRepository(session)

    async def create_order_with_payment(
        self,
        *,
        user: User,
        plan: Plan,
        custom_username: str | None,
        order_kind: str = OrderKind.PURCHASE.value,
        service_id: int | None = None,
        discount_code: str | None = None,
        discount_percent: int = 0,
        discount_amount: int = 0,
    ) -> tuple[Order, object]:
        tracking_code = await self._generate_unique_tracking_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.settings.order_expire_minutes)
        final_amount = max(plan.price - discount_amount, 0)

        order = await self.orders.create(
            user_id=user.id,
            plan_id=plan.id,
            custom_username=custom_username,
            order_kind=order_kind,
            service_id=service_id,
            tracking_code=tracking_code,
            amount=final_amount,
            discount_code=discount_code,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            status=OrderStatus.PENDING_PAYMENT.value,
            expires_at=expires_at,
        )
        payment = await self.payments.create(
            order_id=order.id,
            user_id=user.id,
            amount=final_amount,
        )
        await self.session.commit()
        return order, payment

    async def expire_order_if_unpaid(self, order: Order) -> bool:
        if not self.is_order_expired(order):
            return False
        if order.status != OrderStatus.PENDING_PAYMENT.value:
            return False
        if order.payment and order.payment.receipt_file_id:
            return False
        order.status = OrderStatus.EXPIRED.value
        if order.payment and order.payment.status == PaymentStatus.PENDING.value and not order.payment.receipt_file_id:
            order.payment.status = PaymentStatus.EXPIRED.value
        await self.session.commit()
        return True

    @staticmethod
    def is_order_expired(order: Order) -> bool:
        if order.expires_at is None:
            return False
        expires_at = order.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at

    async def _generate_unique_tracking_code(self) -> str:
        for _ in range(10):
            tracking_code = generate_tracking_code()
            if await self.orders.get_by_tracking_code(tracking_code) is None:
                return tracking_code
        raise RuntimeError("Could not generate a unique tracking code")
