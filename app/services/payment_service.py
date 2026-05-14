from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.models import (
    Order,
    OrderKind,
    OrderStatus,
    Payment,
    PaymentStatus,
    VPNServiceStatus,
    WalletTransactionStatus,
    WalletTransactionType,
)
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.services import ServicesRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.services.affiliate_service import AffiliateService
from app.services.inventory_service import (
    InventoryUnavailableError,
    mark_config_sold,
    release_reserved_config,
)
from app.services.order_service import OrderService
from app.services.referral_service import ReferralService
from app.services.renewal_service import RenewalService
from app.services.settings_service import AppSettingsService
from app.services.vpn_panel import VPNPanelService


class PaymentApprovalError(Exception):
    pass


class PaymentExpiredError(PaymentApprovalError):
    pass


class PaymentAlreadyProcessedError(PaymentApprovalError):
    pass


class InsufficientWalletBalanceError(PaymentApprovalError):
    def __init__(self, *, required_amount: int, wallet_balance: int) -> None:
        self.required_amount = required_amount
        self.wallet_balance = wallet_balance
        super().__init__("Insufficient wallet balance")


@dataclass(frozen=True)
class ApprovedPaymentResult:
    user_telegram_id: int
    order_kind: str
    service_username: str
    plan_title: str
    volume_gb: int
    duration_days: int
    config_link: str | None
    subscription_link: str | None
    new_expire_at: datetime | None = None
    wallet_balance: int | None = None
    waiting_inventory: bool = False
    plan_id: int | None = None


@dataclass(frozen=True)
class RejectedPaymentResult:
    user_telegram_id: int


class PaymentService:
    def __init__(self, session: AsyncSession, vpn_panel: VPNPanelService, settings: Settings | None = None) -> None:
        self.session = session
        self.vpn_panel = vpn_panel
        self.settings = settings
        self.app_settings = AppSettingsService(session)

    async def attach_receipt(self, payment: Payment, receipt_file_id: str) -> None:
        payment.receipt_file_id = receipt_file_id
        payment.status = PaymentStatus.PENDING.value
        await self.session.commit()

    async def approve_payment(self, payment_id: int) -> ApprovedPaymentResult:
        payment = await self._load_payment_for_update(payment_id)
        if payment is None:
            raise PaymentApprovalError("Payment not found")

        order = payment.order
        if order is None:
            raise PaymentApprovalError("Payment is not connected to an order")
        if payment.status != PaymentStatus.PENDING.value:
            raise PaymentAlreadyProcessedError("Payment already processed")
        if self._is_unpaid_order_expired(order, payment):
            order.status = OrderStatus.EXPIRED.value
            payment.status = PaymentStatus.EXPIRED.value
            await release_reserved_config(self.session, order.id)
            await self.session.commit()
            raise PaymentExpiredError("Order expired")
        if order.order_kind == OrderKind.RENEWAL.value and order.renewal_service is None:
            raise PaymentApprovalError("Renewal service not found")

        now = datetime.now(timezone.utc)
        payment.status = PaymentStatus.APPROVED.value
        payment.verified_at = now
        order.status = OrderStatus.PAID.value
        order.paid_at = now
        await self.session.flush()

        order.status = OrderStatus.CREATING_SERVICE.value
        result = await self._complete_order(order, now)

        if not result.waiting_inventory:
            order.status = OrderStatus.COMPLETED.value
            order.completed_at = now
            await self._record_discount_usage(order, now)
            if self.settings is not None:
                await AffiliateService(self.session, self.settings).create_commissions_for_order(order.id)
        await self.session.commit()

        return result

    async def reject_payment(self, payment_id: int) -> RejectedPaymentResult:
        payment = await self._load_payment_for_update(payment_id)
        if payment is None:
            raise PaymentApprovalError("Payment not found")
        if payment.status != PaymentStatus.PENDING.value:
            raise PaymentAlreadyProcessedError("Payment already processed")

        payment.status = PaymentStatus.REJECTED.value
        payment.verified_at = datetime.now(timezone.utc)
        if payment.order:
            payment.order.status = OrderStatus.FAILED.value
            await release_reserved_config(self.session, payment.order.id)
            if self.settings is not None:
                await AffiliateService(self.session, self.settings).reverse_order_commissions(payment.order.id)
        await self.session.commit()
        return RejectedPaymentResult(user_telegram_id=payment.user.telegram_id)

    async def pay_order_from_wallet(self, order_id: int, user_id: int) -> ApprovedPaymentResult:
        payment = await self.session.scalar(
            select(Payment)
            .options(
                joinedload(Payment.user),
                joinedload(Payment.order).joinedload(Order.user),
                joinedload(Payment.order).joinedload(Order.plan),
                joinedload(Payment.order).joinedload(Order.renewal_service),
            )
            .where(Payment.order_id == order_id, Payment.user_id == user_id)
            .with_for_update(of=Payment)
        )
        if payment is None or payment.order is None:
            raise PaymentApprovalError("Payment not found")

        order = payment.order
        user = payment.user
        if payment.status != PaymentStatus.PENDING.value:
            raise PaymentAlreadyProcessedError("Payment already processed")
        if OrderService.is_order_expired(order):
            order.status = OrderStatus.EXPIRED.value
            payment.status = PaymentStatus.EXPIRED.value
            await release_reserved_config(self.session, order.id)
            await self.session.commit()
            raise PaymentExpiredError("Order expired")
        if user.wallet_balance < order.amount:
            raise InsufficientWalletBalanceError(required_amount=order.amount, wallet_balance=user.wallet_balance)

        now = datetime.now(timezone.utc)
        user.wallet_balance -= order.amount
        payment.method = "wallet"
        payment.status = PaymentStatus.APPROVED.value
        payment.verified_at = now
        order.status = OrderStatus.PAID.value
        order.paid_at = now

        transaction_type = (
            WalletTransactionType.RENEWAL.value
            if order.order_kind == OrderKind.RENEWAL.value
            else WalletTransactionType.PURCHASE.value
        )
        await WalletTransactionsRepository(self.session).create(
            user_id=user.id,
            amount=-order.amount,
            type=transaction_type,
            status=WalletTransactionStatus.APPROVED.value,
            description=f"پرداخت سفارش {order.tracking_code}",
            related_order_id=order.id,
            related_payment_id=payment.id,
            approved_at=now,
        )
        await self.session.flush()

        order.status = OrderStatus.CREATING_SERVICE.value
        result = await self._complete_order(order, now)
        if not result.waiting_inventory:
            order.status = OrderStatus.COMPLETED.value
            order.completed_at = now
            await self._record_discount_usage(order, now)
            if self.settings is not None:
                await AffiliateService(self.session, self.settings).create_commissions_for_order(order.id)
        await self.session.commit()

        return ApprovedPaymentResult(
            **{**result.__dict__, "wallet_balance": user.wallet_balance},
        )

    async def _load_payment_for_update(self, payment_id: int) -> Payment | None:
        return await self.session.scalar(
            select(Payment)
            .options(
                joinedload(Payment.user),
                joinedload(Payment.order).joinedload(Order.user),
                joinedload(Payment.order).joinedload(Order.plan),
                joinedload(Payment.order).joinedload(Order.renewal_service),
            )
            .where(Payment.id == payment_id)
            .with_for_update(of=Payment)
        )

    @staticmethod
    def _is_unpaid_order_expired(order: Order, payment: Payment) -> bool:
        return OrderService.is_order_expired(order)

    async def _complete_order(self, order: Order, now: datetime) -> ApprovedPaymentResult:
        if order.order_kind == OrderKind.RENEWAL.value:
            return await self._complete_renewal(order, now)
        return await self._complete_purchase(order, now)

    async def _record_discount_usage(self, order: Order, now: datetime) -> None:
        if not order.discount_code or order.discount_amount <= 0:
            return

        dice_roll = await DiceRollsRepository(self.session).get_by_discount_code(order.discount_code)
        if dice_roll is not None:
            dice_roll.used = True

        await WalletTransactionsRepository(self.session).create(
            user_id=order.user_id,
            amount=order.discount_amount,
            type=WalletTransactionType.DISCOUNT.value,
            status=WalletTransactionStatus.APPROVED.value,
            description=f"تخفیف سفارش {order.tracking_code}",
            related_order_id=order.id,
            related_payment_id=order.payment.id if order.payment else None,
            approved_at=now,
        )

    async def _complete_purchase(self, order: Order, now: datetime) -> ApprovedPaymentResult:
        plan = order.plan
        user = order.user
        username = order.custom_username or f"user{user.telegram_id}"

        try:
            inventory_item = await mark_config_sold(self.session, order.id, user.id)
            config_link = inventory_item.config_link
            subscription_link = inventory_item.subscription_link
            username = order.custom_username or inventory_item.username or username
            config_inventory_id = inventory_item.id
        except InventoryUnavailableError:
            if not (self.settings and self.settings.allow_placeholder_configs):
                order.status = OrderStatus.WAITING_INVENTORY.value
                return ApprovedPaymentResult(
                    user_telegram_id=user.telegram_id,
                    order_kind=OrderKind.PURCHASE.value,
                    service_username=username,
                    plan_title=plan.title,
                    volume_gb=plan.volume_gb,
                    duration_days=plan.duration_days,
                    config_link=None,
                    subscription_link=None,
                    waiting_inventory=True,
                    plan_id=plan.id,
                )
            provisioned = await self.vpn_panel.provision_user(
                username=username,
                volume_gb=plan.volume_gb,
                duration_days=plan.duration_days,
            )
            config_link = provisioned.config_link
            subscription_link = provisioned.subscription_link
            config_inventory_id = None

        services = ServicesRepository(self.session)
        await services.create(
            user_id=user.id,
            order_id=order.id,
            plan_id=plan.id,
            config_inventory_id=config_inventory_id,
            username=username,
            config_link=config_link,
            subscription_link=subscription_link,
            volume_gb=plan.volume_gb,
            duration_days=plan.duration_days,
            expire_at=now + timedelta(days=plan.duration_days),
            status=VPNServiceStatus.ACTIVE.value,
        )

        reward_amount = await self.app_settings.get_referral_reward_amount()
        await ReferralService(self.session).grant_first_purchase_reward(
            user=user,
            order=order,
            amount=reward_amount,
        )

        return ApprovedPaymentResult(
            user_telegram_id=user.telegram_id,
            order_kind=OrderKind.PURCHASE.value,
            service_username=username,
            plan_title=plan.title,
            volume_gb=plan.volume_gb,
            duration_days=plan.duration_days,
            config_link=config_link,
            subscription_link=subscription_link,
            plan_id=plan.id,
        )

    async def _complete_renewal(self, order: Order, now: datetime) -> ApprovedPaymentResult:
        plan = order.plan
        user = order.user
        service = order.renewal_service
        if service is None:
            raise PaymentApprovalError("Renewal service not found")

        new_expire_at = await RenewalService(self.vpn_panel).extend_service(
            service=service,
            plan=plan,
            now=now,
        )

        return ApprovedPaymentResult(
            user_telegram_id=user.telegram_id,
            order_kind=OrderKind.RENEWAL.value,
            service_username=service.username,
            plan_title=plan.title,
            volume_gb=plan.volume_gb,
            duration_days=plan.duration_days,
            config_link=service.config_link,
            subscription_link=service.subscription_link,
            new_expire_at=new_expire_at,
            plan_id=plan.id,
        )
