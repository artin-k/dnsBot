from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderStatus(str, Enum):
    PENDING_USERNAME = "pending_username"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    CREATING_SERVICE = "creating_service"
    COMPLETED = "completed"
    WAITING_INVENTORY = "waiting_inventory"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderKind(str, Enum):
    PURCHASE = "purchase"
    RENEWAL = "renewal"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class VPNServiceStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


class ConfigInventoryStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"
    DISABLED = "disabled"


class WalletTransactionType(str, Enum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    RENEWAL = "renewal"
    REFERRAL_REWARD = "referral_reward"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    DISCOUNT = "discount"
    WITHDRAWAL_REQUEST = "withdrawal_request"
    WITHDRAWAL_PAID = "withdrawal_paid"
    WITHDRAWAL_REJECTED_REFUND = "withdrawal_rejected_refund"


class WalletTransactionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AffiliateBeneficiaryType(str, Enum):
    ROOT_OWNER = "root_owner"
    DIRECT_REFERRER = "direct_referrer"
    MANUAL = "manual"


class AffiliateCommissionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"
    CANCELLED = "cancelled"
    REVERSED = "reversed"


class WalletWithdrawalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    CANCELLED = "cancelled"


class WalletWithdrawalDestinationType(str, Enum):
    CARD = "card"
    SHEBA = "sheba"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AppSetting(TimestampMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="str", server_default="str")
    description: Mapped[str | None] = mapped_column(Text)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    phone_number: Mapped[str | None] = mapped_column(String(32))
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wallet_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    referral_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    referral_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    referral_path: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_root_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    affiliate_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    affiliate_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    affiliate_total_earned: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    affiliate_total_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    referred_by: Mapped[User | None] = relationship("User", remote_side=lambda: [User.id])
    orders: Mapped[list[Order]] = relationship(back_populates="user")
    payments: Mapped[list[Payment]] = relationship(back_populates="user")
    services: Mapped[list[VPNService]] = relationship(back_populates="user")
    wallet_transactions: Mapped[list[WalletTransaction]] = relationship(back_populates="user")
    wallet_withdrawal_requests: Mapped[list[WalletWithdrawalRequest]] = relationship(
        back_populates="user",
        foreign_keys="WalletWithdrawalRequest.user_id",
    )
    processed_withdrawal_requests: Mapped[list[WalletWithdrawalRequest]] = relationship(
        back_populates="processed_by_admin",
        foreign_keys="WalletWithdrawalRequest.processed_by_admin_id",
    )
    sold_config_inventory_items: Mapped[list[ConfigInventory]] = relationship(
        back_populates="sold_to_user",
        foreign_keys="ConfigInventory.sold_to_user_id",
    )
    affiliate_commissions_earned: Mapped[list[AffiliateCommission]] = relationship(
        back_populates="beneficiary",
        foreign_keys="AffiliateCommission.beneficiary_user_id",
    )
    affiliate_commissions_generated: Mapped[list[AffiliateCommission]] = relationship(
        back_populates="buyer",
        foreign_keys="AffiliateCommission.buyer_user_id",
    )
    referral_rewards_sent: Mapped[list[ReferralReward]] = relationship(
        back_populates="referrer",
        foreign_keys="ReferralReward.referrer_id",
    )
    referral_reward_received: Mapped[ReferralReward | None] = relationship(
        back_populates="referred_user",
        foreign_keys="ReferralReward.referred_user_id",
        uselist=False,
    )


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"


    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=720) # 720 hours = 30 days
    volume_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    controld_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    orders: Mapped[list[Order]] = relationship(back_populates="plan")
    services: Mapped[list[VPNService]] = relationship(back_populates="plan")
    config_inventory_items: Mapped[list[ConfigInventory]] = relationship(back_populates="plan")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False)
    custom_username: Mapped[str | None] = mapped_column(String(64))
    order_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=OrderKind.PURCHASE.value,
        server_default=OrderKind.PURCHASE.value,
    )
    service_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_services.id", ondelete="SET NULL"), index=True)
    config_inventory_id: Mapped[int | None] = mapped_column(ForeignKey("config_inventory.id", ondelete="SET NULL"), index=True)
    tracking_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_code: Mapped[str | None] = mapped_column(String(32))
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    discount_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=OrderStatus.PENDING_USERNAME.value,
        server_default=OrderStatus.PENDING_USERNAME.value,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")
    plan: Mapped[Plan] = relationship(back_populates="orders")
    payment: Mapped[Payment | None] = relationship(back_populates="order", uselist=False)
    vpn_service: Mapped[VPNService | None] = relationship(
        back_populates="order",
        uselist=False,
        foreign_keys="VPNService.order_id",
    )
    renewal_service: Mapped[VPNService | None] = relationship(
        foreign_keys=[service_id],
    )
    config_inventory_item: Mapped[ConfigInventory | None] = relationship(
        foreign_keys=[config_inventory_id],
    )


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Telegram user id (BigInteger) as requested in the spec
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False)
    controld_device_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    doh_link: Mapped[str | None] = mapped_column(Text)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")

    plan: Mapped[Plan] = relationship()


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PaymentStatus.PENDING.value,
        server_default=PaymentStatus.PENDING.value,
    )
    authority: Mapped[str | None] = mapped_column(String(255))
    ref_id: Mapped[str | None] = mapped_column(String(255))
    receipt_file_id: Mapped[str | None] = mapped_column(String(255))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order | None] = relationship(back_populates="payment")
    user: Mapped[User] = relationship(back_populates="payments")
    wallet_transactions: Mapped[list[WalletTransaction]] = relationship(back_populates="payment")


class WalletTransaction(TimestampMixin, Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WalletTransactionStatus.PENDING.value,
        server_default=WalletTransactionStatus.PENDING.value,
    )
    description: Mapped[str | None] = mapped_column(Text)
    related_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    related_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), index=True)
    related_withdrawal_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet_withdrawal_requests.id", ondelete="SET NULL"),
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="wallet_transactions")
    order: Mapped[Order | None] = relationship()
    payment: Mapped[Payment | None] = relationship(back_populates="wallet_transactions")
    withdrawal_request: Mapped[WalletWithdrawalRequest | None] = relationship(back_populates="wallet_transactions")


class WalletWithdrawalRequest(TimestampMixin, Base):
    __tablename__ = "wallet_withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WalletWithdrawalStatus.PENDING.value,
        server_default=WalletWithdrawalStatus.PENDING.value,
        index=True,
    )
    destination_type: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_number: Mapped[str] = mapped_column(String(64), nullable=False)
    account_holder_name: Mapped[str | None] = mapped_column(String(255))
    admin_note: Mapped[str | None] = mapped_column(Text)
    user_note: Mapped[str | None] = mapped_column(Text)
    processed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(
        back_populates="wallet_withdrawal_requests",
        foreign_keys=[user_id],
    )
    processed_by_admin: Mapped[User | None] = relationship(
        back_populates="processed_withdrawal_requests",
        foreign_keys=[processed_by_admin_id],
    )
    wallet_transactions: Mapped[list[WalletTransaction]] = relationship(back_populates="withdrawal_request")


class VPNService(TimestampMixin, Base):
    __tablename__ = "vpn_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    config_inventory_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_inventory.id", ondelete="SET NULL"),
        index=True,
    )
    controld_device_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    is_test_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    config_link: Mapped[str | None] = mapped_column(Text)
    subscription_link: Mapped[str | None] = mapped_column(Text)
    volume_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VPNServiceStatus.ACTIVE.value,
        server_default=VPNServiceStatus.ACTIVE.value,
    )
    user: Mapped[User] = relationship(back_populates="services")
    order: Mapped[Order | None] = relationship(back_populates="vpn_service", foreign_keys=[order_id])
    plan: Mapped[Plan | None] = relationship(back_populates="services")
    config_inventory_item: Mapped[ConfigInventory | None] = relationship(
        foreign_keys=[config_inventory_id],
    )

class ConfigInventory(TimestampMixin, Base):
    __tablename__ = "config_inventory"
    __table_args__ = (
        CheckConstraint(
            "(config_link IS NOT NULL AND config_link <> '') OR "
            "(subscription_link IS NOT NULL AND subscription_link <> '')",
            name="ck_config_inventory_has_link",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    config_link: Mapped[str | None] = mapped_column(Text)
    subscription_link: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConfigInventoryStatus.AVAILABLE.value,
        server_default=ConfigInventoryStatus.AVAILABLE.value,
        index=True,
    )
    reserved_by_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    sold_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    plan: Mapped[Plan] = relationship(back_populates="config_inventory_items")
    reserved_order: Mapped[Order | None] = relationship(foreign_keys=[reserved_by_order_id])
    sold_to_user: Mapped[User | None] = relationship(
        back_populates="sold_config_inventory_items",
        foreign_keys=[sold_to_user_id],
    )


class ReferralReward(TimestampMixin, Base):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    referred_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)

    referrer: Mapped[User] = relationship(
        back_populates="referral_rewards_sent",
        foreign_keys=[referrer_id],
    )
    referred_user: Mapped[User] = relationship(
        back_populates="referral_reward_received",
        foreign_keys=[referred_user_id],
    )
    order: Mapped[Order] = relationship()


class AffiliateCommission(TimestampMixin, Base):
    __tablename__ = "affiliate_commissions"
    __table_args__ = (
        UniqueConstraint("order_id", "beneficiary_user_id", name="uq_affiliate_commissions_order_beneficiary"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False)
    buyer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    beneficiary_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    beneficiary_type: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    base_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    percent: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    commission_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AffiliateCommissionStatus.APPROVED.value,
        server_default=AffiliateCommissionStatus.APPROVED.value,
    )
    description: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship()
    buyer: Mapped[User] = relationship(
        back_populates="affiliate_commissions_generated",
        foreign_keys=[buyer_user_id],
    )
    beneficiary: Mapped[User] = relationship(
        back_populates="affiliate_commissions_earned",
        foreign_keys=[beneficiary_user_id],
    )


class TestAccount(TimestampMixin, Base):
    __tablename__ = "test_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    config_link: Mapped[str] = mapped_column(Text, nullable=False)
    subscription_link: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    max_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24, server_default="24")

    claims: Mapped[list[TestAccountClaim]] = relationship(back_populates="test_account")


class TestAccountClaim(Base):
    __tablename__ = "test_account_claims"
    __table_args__ = (UniqueConstraint("user_id", name="uq_test_account_claims_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    test_account_id: Mapped[int] = mapped_column(ForeignKey("test_accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
    test_account: Mapped[TestAccount] = relationship(back_populates="claims")


class DiceRoll(TimestampMixin, Base):
    __tablename__ = "dice_rolls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    dice_value: Mapped[int] = mapped_column(Integer, nullable=False)
    won: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    discount_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class MandatoryChannel(TimestampMixin, Base):
    __tablename__ = "mandatory_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_link: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
