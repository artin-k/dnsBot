from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import OrderKind, OrderStatus, VPNServiceStatus, WalletTransactionStatus, WalletTransactionType
from app.utils.money import format_money

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

ORDER_STATUS_LABELS = {
    OrderStatus.PENDING_USERNAME.value: "در انتظار نام کاربری",
    OrderStatus.PENDING_PAYMENT.value: "در انتظار پرداخت",
    OrderStatus.PAID.value: "پرداخت شده",
    OrderStatus.CREATING_SERVICE.value: "در حال ساخت سرویس",
    OrderStatus.COMPLETED.value: "تکمیل شده",
    OrderStatus.EXPIRED.value: "منقضی شده",
    OrderStatus.CANCELLED.value: "لغو شده",
    OrderStatus.FAILED.value: "ناموفق",
}

ORDER_TYPE_LABELS = {
    OrderKind.PURCHASE.value: "خرید جدید",
    OrderKind.RENEWAL.value: "تمدید",
}

SERVICE_STATUS_LABELS = {
    VPNServiceStatus.ACTIVE.value: "فعال",
    VPNServiceStatus.EXPIRED.value: "منقضی شده",
    VPNServiceStatus.DISABLED.value: "غیرفعال",
}

WALLET_TRANSACTION_TYPE_LABELS = {
    WalletTransactionType.TOPUP.value: "شارژ کیف پول",
    WalletTransactionType.PURCHASE.value: "خرید اشتراک",
    WalletTransactionType.RENEWAL.value: "تمدید سرویس",
    WalletTransactionType.REFERRAL_REWARD.value: "پاداش زیرمجموعه‌گیری",
    WalletTransactionType.ADMIN_ADJUSTMENT.value: "تنظیم دستی مدیریت",
    WalletTransactionType.DISCOUNT.value: "تخفیف",
}

WALLET_TRANSACTION_STATUS_LABELS = {
    WalletTransactionStatus.PENDING.value: "در انتظار تایید",
    WalletTransactionStatus.APPROVED.value: "تایید شده",
    WalletTransactionStatus.REJECTED.value: "رد شده",
    WalletTransactionStatus.CANCELLED.value: "لغو شده",
}


def format_order_status_fa(status: str | None) -> str:
    return ORDER_STATUS_LABELS.get(status or "", status or "-")


def format_order_type_fa(order_type: str | None) -> str:
    return ORDER_TYPE_LABELS.get(order_type or OrderKind.PURCHASE.value, "خرید جدید")


def format_service_status_fa(status: str | None) -> str:
    return SERVICE_STATUS_LABELS.get(status or "", status or "-")


def format_wallet_transaction_type_fa(transaction_type: str | None) -> str:
    return WALLET_TRANSACTION_TYPE_LABELS.get(transaction_type or "", transaction_type or "-")


def format_wallet_transaction_status_fa(status: str | None) -> str:
    return WALLET_TRANSACTION_STATUS_LABELS.get(status or "", status or "-")


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")


def format_remaining_time(total_seconds: int) -> str:
    total_seconds = max(total_seconds, 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _seconds = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours} ساعت و {minutes} دقیقه"
    if hours:
        return f"{hours} ساعت"
    return f"{minutes} دقیقه"
