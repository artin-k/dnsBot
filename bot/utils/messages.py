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
    """Renders the clean DNS delivery card with DNS at the top and instructions at the bottom."""
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
            ).strftime("%Y/%m/%d - %H:%M:%S")
        except Exception:
            expire_str = expire_at.strftime("%Y-%m-%d %H:%M:%S")

    duration_text = calculate_remaining_time_fa(expire_at)

    # Build AdGuard Home Section
    agh_primary = settings.adguard_primary_dns or "94.183.180.215"
    agh_secondary = settings.adguard_secondary_dns or "0.0.0.0"
    agh_doh = settings.adguard_doh_url

    adguard_block = f"""
🔹 Primary: <code>{escape(agh_primary)}</code>
🔹 Secondary: <code>{escape(agh_secondary)}</code>"""
    if agh_doh:
        adguard_block += f"\n🌐 DoH: <code>{escape(agh_doh)}</code>"

    return f"""{title_prefix}

🔹 <b>تاریخ انقضاء پلن:</b> <code>{escape(expire_str)}</code>
🔷 <b>زمان باقی‌مانده:</b> {escape(duration_text)}
🎮 <b>سرویس/بازی:</b> <b>{escape(service_display)}</b>
🗺️ <b>سرور انتخابی:</b> <b>{escape(country_display)}</b>
━━━━━━━━━━━━━━━━━━━━━

{adguard_block}

🔹 Primary: <code>{escape(ipv4_primary)}</code>
🔹 Secondary: <code>{escape(ipv4_secondary)}</code>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>راهنمای ثبت IP:</b>
1️⃣ ابتدا موبایل و کنسول را به یک شبکه اینترنت مشترک متصل کنید.
2️⃣ سپس، در حالی که VPN یا فیلترشکن خاموش است، روی گزینه «ثبت IP» کلیک کنید.
✅ پس از ثبت موفق IP، DNS برای شما فعال خواهد شد.
⚠️ <b>توجه:</b> تا زمانی که IP شما ثبت نشده باشد، امکان استفاده از DNSها وجود نخواهد داشت."""


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
    """One-line helper that renders text, attaches simple keyboard, and schedules auto-deletion."""
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