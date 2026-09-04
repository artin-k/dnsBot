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


# Module-level alias for backward compatibility across modules
format_datetime = format_datetime_fa