from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderStatus(str, Enum):
    PENDING_USERNAME = "pending_username"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    CREATING_SERVICE = "creating_service"
    COMPLETED = "completed"
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


class WalletTransactionType(str, Enum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    RENEWAL = "renewal"
    REFERRAL_REWARD = "referral_reward"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    DISCOUNT = "discount"


class WalletTransactionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


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


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    phone_number: Mapped[str | None] = mapped_column(String(32))
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wallet_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    referral_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    referred_by: Mapped[User | None] = relationship("User", remote_side=lambda: [User.id])
    orders: Mapped[list[Order]] = relationship(back_populates="user")
    payments: Mapped[list[Payment]] = relationship(back_populates="user")
    services: Mapped[list[VPNService]] = relationship(back_populates="user")
    wallet_transactions: Mapped[list[WalletTransaction]] = relationship(back_populates="user")
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
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    volume_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    orders: Mapped[list[Order]] = relationship(back_populates="plan")
    services: Mapped[list[VPNService]] = relationship(back_populates="plan")


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


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
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
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
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
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="wallet_transactions")
    order: Mapped[Order | None] = relationship()
    payment: Mapped[Payment | None] = relationship(back_populates="wallet_transactions")


class VPNService(TimestampMixin, Base):
    __tablename__ = "vpn_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    config_link: Mapped[str | None] = mapped_column(Text)
    subscription_link: Mapped[str | None] = mapped_column(Text)
    volume_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VPNServiceStatus.ACTIVE.value,
        server_default=VPNServiceStatus.ACTIVE.value,
    )

    user: Mapped[User] = relationship(back_populates="services")
    order: Mapped[Order] = relationship(back_populates="vpn_service", foreign_keys=[order_id])
    plan: Mapped[Plan] = relationship(back_populates="services")


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
