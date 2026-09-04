# bot/routers/services.py
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo
import jdatetime
import structlog

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SLOT_CONFIGS, Settings, get_settings
from app.models import VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService
from app.services.settings_service import AppSettingsService
from app.utils.auto_clean import schedule_message_deletion
from app.utils.formatting import calculate_remaining_time_fa, format_datetime
from bot import menu_actions

router = Router(name="services")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url


def is_service_active(service: VPNService) -> bool:
    """A service is ACTIVE if its status is not 'disabled' and expire_at is in the future."""
    if not service or service.status == "disabled":
        return False
    now = datetime.now(timezone.utc)
    expire_at = service.expire_at
    if not expire_at:
        return False
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return expire_at > now


def format_service_item_display(service: VPNService, index: int) -> str:
    """Parses active subscription metadata cleanly for localized server names."""
    raw_username = service.username or ""
    service_display = "کل ترافیک اینترنت (Default)"
    country_display = "پیش‌فرض"
    username_part = raw_username

    if "|" in raw_username:
        parts = raw_username.split("|")
        username_part = parts[0]
        service_pk = parts[1] if len(parts) > 1 else "default"
        slot_num_str = parts[2] if len(parts) > 2 else "1"

        if service_pk != "default":
            service_display = service_pk.capitalize()

        if slot_num_str and slot_num_str.isdigit():
            slot_num = int(slot_num_str)
            if slot_num in SLOT_CONFIGS:
                country_display = SLOT_CONFIGS[slot_num]["name"]

    active = is_service_active(service)
    status_fa = "🟢 فعال" if active else "🔴 منقضی شده"

    return f"""<b>{index}. 👤 نام دستگاه:</b> <code>{escape(username_part)}</code>
🎮 <b>برنامه/بازی:</b> {escape(service_display)}
🗺 <b>سرور (کشور):</b> {escape(country_display)}
⚡ <b>پلن:</b> {escape(service.plan.title if service.plan else "اکانت تست")}
🗓 <b>تاریخ انقضا:</b> {format_datetime(service.expire_at)}
📌 <b>وضعیت:</b> {status_fa}
"""


def build_ip_registration_keyboard(
    device_id: str,
    support_username: str = "",
    video_link: str = "",
) -> InlineKeyboardMarkup:
    """Builds IP registration keyboard with Telegram 9.4+ color styles."""
    builder = InlineKeyboardBuilder()

    base_url = WEB_SERVER_BASE_URL.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"

    dev_str = str(device_id or "unknown").strip()
    update_url = f"{base_url}/update-ip/{dev_str}"

    builder.button(
        text="✳️ ثبت آی‌پی اتوماتیک ✳️",
        url=update_url,
        style="success",
    )
    builder.button(
        text="✳️ ثبت آی‌پی اتوماتیک ۲ (کمکی) ✳️",
        url=update_url,
        style="success",
    )
    builder.button(
        text="🤖 ثبت آی‌پی دستی",
        callback_data=f"manual_ip_reg:{dev_str}",
        style="primary",
    )

    if support_username:
        clean_sup = support_username.removeprefix("@").strip()
        if clean_sup:
            builder.button(text="☎️ پشتیبانی آنلاین", url=f"https://t.me/{clean_sup}")

    if video_link:
        clean_vid = video_link.strip()
        if clean_vid:
            if not clean_vid.startswith(("http://", "https://")):
                clean_vid = f"https://{clean_vid}"
            builder.button(text="🎥 ویدیو آموزشی راهنما", url=clean_vid)

    builder.adjust(1)
    return builder.as_markup()


async def create_secure_ip_update_keyboard(
    session: AsyncSession,
    service_id_or_device_id: int | str,
) -> InlineKeyboardMarkup:
    """Unified keyboard generator with support username fallback."""
    if isinstance(service_id_or_device_id, int):
        service = await session.get(VPNService, service_id_or_device_id)
        device_id = service.controld_device_id if service else "unknown"
    else:
        device_id = str(service_id_or_device_id)

    app_settings = AppSettingsService(session)
    support_username = await app_settings.get_support_username()
    video_link = await app_settings.get_teaching_video_link()

    if not support_username:
        settings = get_settings()
        if settings.root_admin_telegram_id:
            root_user = await UsersRepository(session).get_by_telegram_id(settings.root_admin_telegram_id)
            if root_user and root_user.telegram_username:
                support_username = root_user.telegram_username

    return build_ip_registration_keyboard(
        device_id=device_id,
        support_username=support_username,
        video_link=video_link,
    )


def _get_service_manage_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """Active service management menu with stylized colored tiers."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔗 لینک‌ها و آدرس‌های اتصال",
        callback_data=f"manage_links:{service_id}",
        style="primary",
    )
    builder.button(
        text="🗺 تغییر لوکیشن سرور",
        callback_data=f"change_default_loc_select:{service_id}",
        style="primary",
    )
    builder.button(
        text="📊 وضعیت زنده اتصال",
        callback_data=f"manage_status:{service_id}",
    )
    builder.button(
        text="🔙 بازگشت به لیست اشتراک‌ها",
        callback_data="my_services_page:0",
    )
    builder.adjust(1)
    return builder.as_markup()


async def _show_my_services_page(
    callback_or_message: CallbackQuery | Message,
    page: int,
    session: AsyncSession,
) -> None:
    """Renders parsed services per page (sends ONE single message)."""
    user_id = callback_or_message.from_user.id
    user = await UsersRepository(session).get_by_telegram_id(user_id)
    if not user:
        return

    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        msg = "شما هنوز هیچ سرویس یا اشتراکی تهیه نکرده‌اید."
        if isinstance(callback_or_message, CallbackQuery) and callback_or_message.message:
            await callback_or_message.message.answer(msg)
        else:
            await callback_or_message.answer(msg)
        return

    def service_sort_key(s: VPNService):
        active = is_service_active(s)
        exp = s.expire_at
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            ts = exp.timestamp()
        else:
            ts = 0.0
        return (0 if active else 1, -ts)

    services.sort(key=service_sort_key)

    limit = 3
    start_idx = page * limit
    end_idx = start_idx + limit
    page_services = services[start_idx:end_idx]
    has_next = len(services) > end_idx

    lines = [f"🛍 <b>اشتراک‌های DNS شما | صفحه {page + 1} از {((len(services) - 1) // limit) + 1}</b>\n"]

    builder = InlineKeyboardBuilder()
    for idx, service in enumerate(page_services, start=start_idx + 1):
        lines.append(format_service_item_display(service, idx))
        raw_name = (service.username or "دستگاه").split("|")[0].strip()

        if is_service_active(service):
            builder.row(
                InlineKeyboardButton(
                    text="✳️ ثبت آی‌پی سریع",
                    url=f"{WEB_SERVER_BASE_URL}/update-ip/{service.controld_device_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="🛠 مدیریت",
                    callback_data=f"manage_service:{service.id}",
                    style="primary",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"🛒 تمدید اشتراک: {raw_name}",
                    callback_data="buy_back_to_plans",
                    style="primary",
                )
            )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"my_services_page:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"my_services_page:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="buy_back_to_menu", style="danger"))

    text_content = "\n".join(lines)

    if isinstance(callback_or_message, CallbackQuery) and callback_or_message.message:
        await callback_or_message.message.edit_text(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await callback_or_message.answer(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("my_services_page:"), StateFilter("*"))
async def handle_my_services_page(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await _show_my_services_page(callback, page, session)


@router.callback_query(F.data.startswith("manage_service:"), StateFilter("*"))
async def handle_manage_service(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    if callback.message is None:
        return
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    if not is_service_active(service):
        builder = InlineKeyboardBuilder()
        builder.button(text="🛒 خرید / تمدید اشتراک", callback_data="buy_back_to_plans")
        builder.button(text="🔙 بازگشت به لیست", callback_data="my_services_page:0")
        builder.adjust(1)
        await callback.message.edit_text(
            "❌ <b>این اشتراک منقضی شده است.</b>\n\n"
            "برای ادامه استفاده از دی‌ان‌اس و تغییر لوکیشن، لطفاً اشتراک جدید تهیه یا تمدید کنید.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("manage_links:"), StateFilter("*"))
async def handle_manage_links(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    if callback.message is None:
        return
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    if not is_service_active(service):
        await callback.answer("❌ این اشتراک منقضی شده است. امکان دریافت لینک وجود ندارد.", show_alert=True)
        return

    raw_username = service.username or ""
    device_name = raw_username.split("|")[0].strip()
    service_display = "کل ترافیک اینترنت (Default)"

    if "|" in raw_username:
        parts = raw_username.split("|")
        service_pk = parts[1] if len(parts) > 1 else "default"
        if service_pk != "default":
            service_display = service_pk.capitalize()

    ipv4_primary = "76.76.2.162"
    ipv4_secondary = "76.76.10.162"
    country_name = "پیش‌فرض"
    for num, config in SLOT_CONFIGS.items():
        if config["device_id"] == service.controld_device_id:
            ipv4_primary = config["dns_primary"]
            ipv4_secondary = config["dns_secondary"]
            country_name = config["name"]
            break

    from bot.utils.messages import render_dns_delivery_text

    text = render_dns_delivery_text(
        expire_at=service.expire_at,
        ipv4_primary=ipv4_primary,
        ipv4_secondary=ipv4_secondary,
        service_display=service_display,
        country_display=country_name,
        title_prefix=f"📊 <b>مشخصات و دی‌ان‌اس‌های سرویس {escape(device_name)}</b>",
    )

    markup = await create_secure_ip_update_keyboard(session, service.id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await schedule_message_deletion(callback.bot, callback.message.chat.id, callback.message.message_id, delay_seconds=7200)


@router.callback_query(F.data.startswith("manage_status:"), StateFilter("*"))
async def handle_manage_status(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    if callback.message is None:
        return
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")


async def _show_default_loc_page(
    callback: CallbackQuery,
    service: VPNService,
    settings: Settings,
) -> None:
    """Renders location switcher highlighting active server between Germany & Turkey."""
    builder = InlineKeyboardBuilder()

    current_device = service.controld_device_id
    is_germany_active = current_device == SLOT_CONFIGS[1]["device_id"]
    is_turkey_active = current_device == SLOT_CONFIGS[5]["device_id"]

    if is_germany_active:
        builder.button(
            text="🟢 🇩🇪 آلمان (سرور فعال فعلی شما)",
            callback_data=f"apply_def_loc:{service.id}:1",
            style="success",
        )
    else:
        builder.button(
            text="🔄 🇩🇪 انتقال به آلمان (فرانکفورت)",
            callback_data=f"apply_def_loc:{service.id}:1",
            style="primary",
        )

    if is_turkey_active:
        builder.button(
            text="🟢 🇹🇷 ترکیه (سرور فعال فعلی شما)",
            callback_data=f"apply_def_loc:{service.id}:5",
            style="success",
        )
    else:
        builder.button(
            text="🔄 🇹🇷 انتقال به ترکیه (استانبول)",
            callback_data=f"apply_def_loc:{service.id}:5",
            style="primary",
        )

    builder.button(
        text="🔙 بازگشت به مدیریت",
        callback_data=f"manage_service:{service.id}",
        style="danger",
    )
    builder.adjust(1)

    active_name = (
        "🇩🇪 آلمان (فرانکفورت)"
        if is_germany_active
        else "🇹🇷 ترکیه (استانبول)"
        if is_turkey_active
        else "سایر سرورها"
    )

    text = f"""🗺 <b>تغییر لوکیشن سرور دی‌ان‌اس</b>

👤 <b>نام دستگاه:</b> <code>{(service.username or '').split('|')[0]}</code>
📌 <b>سرور فعال شما:</b> <b>{active_name}</b>

<blockquote>💡 <b>راهنما:</b>
• سرور فعال با رنگ سبز 🟢 مشخص شده است.
• با کلیک روی سرور دیگر، دسترسی شما فوراً به کشور جدید منتقل شده و آدرس‌های DNS جدید برای شما ارسال می‌گردد.</blockquote>"""

    if callback.message:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("change_default_loc_select:"), StateFilter("*"))
async def handle_change_default_loc_select(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)

    if not service or not is_service_active(service):
        await callback.answer("❌ این اشتراک منقضی شده است. امکان تغییر لوکیشن وجود ندارد.", show_alert=True)
        return

    await _show_default_loc_page(callback, service, settings=settings)


@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    slot_num = int(parts[2])

    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس یافت نشد.")
        return

    if not is_service_active(service):
        await callback.message.answer(
            "❌ <b>این اشتراک منقضی شده است.</b>\n\n"
            "امکان تغییر لوکیشن برای سرویس‌های منقضی شده وجود ندارد. لطفاً ابتدا اقدام به تمدید نمایید.",
            parse_mode="HTML",
        )
        return

    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات نامعتبر است.")
        return

    new_device_id = SLOT_CONFIGS[slot_num]["device_id"]
    new_pop_name = SLOT_CONFIGS[slot_num]["name"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    if service.controld_device_id == new_device_id:
        await callback.message.answer(f"ℹ️ اشتراک شما در حال حاضر روی سرور {escape(new_pop_name)} فعال است.")
        return

    await callback.message.edit_text(
        f"⚙️ در حال انتقال لوکیشن اشتراک شما به سرور {escape(new_pop_name)}...",
        reply_markup=None,
    )

    controld = ControlDService(settings)
    old_device_id = service.controld_device_id
    user_ip = service.authorized_ip

    if old_device_id and user_ip:
        try:
            logger.info("surgically_deauthorizing_old_slot_ip", service_id=service.id, old_device_id=old_device_id, ip=user_ip)
            await controld.deauthorize_ip(old_device_id, user_ip)
        except Exception as exc:
            logger.warning("old_slot_ip_deauthorization_failed_proceeding", service_id=service.id, error=str(exc))

    if user_ip:
        try:
            logger.info("authorizing_new_slot", service_id=service.id, new_device_id=new_device_id, ip=user_ip)
            await controld.authorize_ip(new_device_id, user_ip)
        except Exception as exc:
            logger.error("new_slot_ip_authorization_failed", service_id=service.id, error=str(exc))

    raw_username = service.username.split("|")[0].strip() if service.username else "دستگاه"
    service.username = f"{raw_username}|default|{slot_num}"
    service.controld_device_id = new_device_id
    await session.commit()

    from bot.utils.messages import render_dns_delivery_text

    success_text = render_dns_delivery_text(
        expire_at=servi