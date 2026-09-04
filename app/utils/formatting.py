# app/utils/formatting.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import jdatetime


def format_money(amount: int | float) -> str:
    """Formats an integer to a string with comma separators."""
    return f"{amount:,}"


def calculate_remaining_time_fa(expire_at: datetime | None) -> str:
    """Calculates remaining time and returns a Persian string."""
    if not expire_at:
        return "۳۰ روز"

    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)

    delta = expire_at - now
    total_seconds = delta.total_seconds()

    if total_seconds <= 0:
        return "پایان یافته"

    total_hours = int(total_seconds // 3600)

    if total_hours >= 24:
        return f"{total_hours // 24} روز"
    if total_hours > 0:
        return f"{total_hours} ساعت"

    return f"{int(total_seconds // 60)} دقیقه"


def format_duration_fa(hours: int) -> str:
    """Converts duration hours to a clean Persian string (days if divisible by 24, else hours)."""
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} روز"
    return f"{hours} ساعت"


def format_datetime_fa(value: datetime | None) -> str:
    """Converts standard datetimes to Shamsi (Jalali) format in Asia/Tehran timezone."""
    if value is None:
        return "نامشخص"

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    tehran_tz = ZoneInfo("Asia/Tehran")
    localized = value.astimezone(tehran_tz)

    try:
        naive_tehran = localized.replace(tzinfo=None)
        return jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        return localized.strftime("%Y-%m-%d %H:%M:%S")


# Export both names so any router can import either one
format_datetime = format_datetime_fa


# --- Helpers for wallet and order routers ---
def calculate_commission_amount(base_amount: int, percent: float) -> int:
    if percent <= 0 or base_amount <= 0:
        return 0
    return int(base_amount * (percent / 100.0))


def format_wallet_transaction_type_fa(tx_type: str) -> str:
    mapping = {
        "topup": "شارژ کیف پول",
        "purchase": "خرید اشتراک",
        "renewal": "تمدید اشتراک",
        "referral_reward": "پاداش زیرمجموعه‌گیری",
        "admin_adjustment": "تغییر توسط ادمین",
        "discount": "تخفیف",
        "withdrawal_request": "درخواست برداشت",
        "withdrawal_paid": "برداشت پرداخت‌شده",
        "withdrawal_rejected_refund": "بازگشت وجه برداشت ردشده",
    }
    return mapping.get(tx_type, tx_type)


def format_wallet_transaction_status_fa(status: str) -> str:
    mapping = {
        "pending": "در انتظار",
        "approved": "تایید شده",
        "rejected": "رد شده",
        "cancelled": "لغو شده",
    }
    return mapping.get(status, status)


def format_order_status_fa(status: str) -> str:
    mapping = {
        "pending_username": "در انتظار نام کاربری",
        "pending_payment": "در انتظار پرداخت",
        "paid": "پرداخت شده",
        "creating_service": "در حال ساخت سرویس",
        "completed": "تکمیل شده",
        "waiting_inventory": "در انتظار موجودی",
        "expired": "منقضی شده",
        "cancelled": "لغو شده",
        "failed": "ناموفق",
    }
    return mapping.get(status, status)


def format_order_type_fa(kind: str | None) -> str:
    if kind == "renewal":
        return "تمدید"
    return "خرید"

# --- Remaining Time Aliases ---
format_remaining_time = calculate_remaining_time_fa
format_remaining_time_fa = calculate_remaining_time_fa


# --- Traffic / Volume Formatters (for legacy panel views) ---
def format_traffic(volume_gb: int | float | None) -> str:
    if not volume_gb or volume_gb <= 0:
        return "نامحدود"
    return f"{volume_gb} گیگابایت"

format_volume = format_traffic
format_volume_gb = format_traffic

# --- Service Status Formatters ---
def format_service_status_fa(status: str | None) -> str:
    mapping = {
        "active": "🟢 فعال",
        "expired": "🔴 منقضی شده",
        "disabled": "⛔ غیرفعال",
    }
    return mapping.get(str(status).lower() if status else "", "نامشخص")

format_service_status = format_service_status_fa