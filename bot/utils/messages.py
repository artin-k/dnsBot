# bot/utils/messages.py
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo
import jdatetime
from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import VPNService
from app.utils.formatting import calculate_remaining_time_fa
from bot.utils.auto_clean import schedule_message_deletion


def render_dns_delivery_text(
    *,
    expire_at: datetime | None,
    ipv4_primary: str,
    ipv4_secondary: str,
    service_display: str = "کل ترافیک اینترنت (Default)",
    country_display: str = "پیش‌فرض",
    title_prefix: str = "✅ <b>اشتراک DNS شما با موفقیت فعال شد!</b>",
) -> str:
    """Single Source of Truth for all DNS delivery/purchase/location messages."""
    settings = get_settings()

    # Format Shamsi Expiration
    expire_str = "-"
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        try:
            tehran_expire = expire_at.astimezone(ZoneInfo("Asia/Tehran"))
            expire_str = jdatetime.datetime.fromgregorian(
                datetime=tehran_expire.replace(tzinfo=None)
            ).strftime("%Y/%m/%d — %H:%M:%S")
        except Exception:
            expire_str = expire_at.strftime("%Y-%m-%d %H:%M:%S")

    duration_text = calculate_remaining_time_fa(expire_at)

    # Build AdGuard Home Section
    agh_primary = settings.adguard_primary_dns or "94.183.180.215"
    agh_secondary = settings.adguard_secondary_dns or "94.183.180.215"
    agh_doh = settings.adguard_doh_url

    adguard_block = f"""
🔹 Primary: <code>{escape(agh_primary)}</code>
🔹 Secondary: <code>{escape(agh_secondary)}</code>"""
    if agh_doh:
        adguard_block += f"\n🌐 DoH: <code>{escape(agh_doh)}</code>"

    return f"""{title_prefix}

📋 <b>اطلاعات اشتراک:</b>
🔹 <b>تاریخ انقضاء:</b> <code>{escape(expire_str)}</code>
🔷 <b>زمان باقی‌مانده:</b> <b>{escape(duration_text)}</b>
🎮 <b>سرویس انتخابی:</b> <code>{escape(service_display)}</code>
🗺️ <b>سرور لوکیشن:</b> <b>{escape(country_display)}</b>
━━━━━━━━━━━━━━━━━━━━━
🎮 <b>دی‌ان‌اس های  گیمینگ :</b>

{adguard_block}


🔹 Primary: <code>{escape(ipv4_primary)}</code>
🔹 Secondary: <code>{escape(ipv4_secondary)}</code>


━━━━━━━━━━━━━━━━━━━━━
<blockquote>📋 <b>مراحل فعال‌سازی (بسیار مهم):</b>

1️⃣ <b>تنظیم DNS:</b> آدرس‌های بالا را در دستگاه خود وارد کنید (Control D برای پینگ گیمینگ و AdGuard برای وب‌گردی و حذف تبلیغات).
2️⃣ <b>شبکه مشترک:</b> موبایل و سیستم/کنسول را به یک مودم یا وای‌فای مشترک متصل کنید.
3️⃣ <b>خاموشی VPN:</b> فیلترشکن و پروکسی تلگرام خود را کاملاً خاموش کنید.
4️⃣ <b>ثبت آی‌پی:</b> روی دکمه سبز رنگ <b>«ثبت آی‌پی اتوماتیک»</b> زیر کلیک کنید.

💡 <i>نکته: شما می‌توانید لوکیشن سرور را بعد از خرید به تعداد نامحدود از بخش «اشتراک‌های من» تغییر دهید.</i>
⚠️ <i>در صورت عدم ثبت آی‌پی بدون فیلترشکن، دسترسی به DNS فعال نخواهد شد.</i></blockquote>"""


async def send_dns_delivery_card(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    service: VPNService,
    *,
    title_prefix: str = "✅ <b>اشتراک DNS شما با موفقیت فعال شد!</b>",
    ipv4_primary: str = "76.76.2.162",
    ipv4_secondary: str = "76.76.10.162",
    service_display: str = "کل ترافیک اینترنت (Default)",
    country_display: str = "پیش‌فرض",
    delay_seconds: int = 7200,
) -> Message:
    """One-line helper that renders text, attaches dynamic keyboard, and schedules auto-deletion."""
    from bot.routers.services import create_secure_ip_update_keyboard

    text = render_dns_delivery_text(
        expire_at=service.expire_at,
        ipv4_primary=ipv4_primary,
        ipv4_secondary=ipv4_secondary,
        service_display=service_display,
        country_display=country_display,
        title_prefix=title_prefix,
    )
    markup = await create_secure_ip_update_keyboard(session, service.id)

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        parse_mode="HTML",
    )
    await schedule_message_deletion(bot, sent.chat.id, sent.message_id, delay_seconds=delay_seconds)
    return sent