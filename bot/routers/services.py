# bot/routers/services.py
import ipaddress
import re
import uuid
from datetime import datetime, timedelta, timezone
from html import escape

import structlog
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import SLOT_CONFIGS, Settings, get_settings
from app.database import async_session_maker
from app.models import IPAuthToken, Plan, VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.adguard import AdGuardHomeService
from app.services.controld import ControlDService
from app.services.ip_manager import update_device_ip_safe
from app.services.settings_service import AppSettingsService
from app.services.vpn_detector import verify_user_ip
from app.utils.formatting import format_datetime
from bot import menu_actions, texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.utils.auto_clean import schedule_message_deletion
from bot.utils.messages import render_dns_delivery_text

router = Router(name="services")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url


# ============================================================================
# FSM STATES
# ============================================================================
class ManualIPState(StatesGroup):
    waiting_for_ip = State()


# ============================================================================
# HELPERS
# ============================================================================
def is_service_active(service: VPNService) -> bool:
    if not service or service.status == "disabled":
        return False
    now = datetime.now(timezone.utc)
    expire_at = service.expire_at
    if not expire_at:
        return False
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return expire_at > now


def _parse_callback_parts(callback: CallbackQuery, prefix: str, count: int) -> list[str] | None:
    parts = (callback.data or "").split(":")
    if len(parts) != count or parts[0] != prefix:
        return None
    return parts


async def _get_owned_service(
    callback: CallbackQuery,
    session: AsyncSession,
    service_id: int,
) -> VPNService | None:
    if service_id <= 0 or callback.from_user is None:
        return None

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return None

    return await ServicesRepository(session).get_user_service(service_id, user.id)


async def _get_owned_service_from_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    prefix: str,
    count: int,
) -> VPNService | None:
    parts = _parse_callback_parts(callback, prefix, count)
    if parts is None:
        return None

    try:
        service_id = int(parts[1])
    except ValueError:
        return None

    return await _get_owned_service(callback, session, service_id)


async def _reject_invalid_service_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("❌ سرویس یافت نشد یا دسترسی به آن مجاز نیست.")


def format_service_item_display(service: VPNService, index: int) -> str:
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


def _get_service_manage_keyboard(service_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 لینک‌های اتصال و ثبت آی‌پی", callback_data=f"manage_links:{service_id}")
    builder.button(text="🗺 تغییر لوکیشن سرور", callback_data=f"change_default_loc_select:{service_id}")
    builder.button(text="📊 وضعیت سرویس", callback_data=f"manage_status:{service_id}")
    builder.button(text="🔙 بازگشت به لیست", callback_data="my_services_page:0")
    builder.adjust(1)
    return builder.as_markup()


async def create_secure_ip_update_keyboard(
    session: AsyncSession,
    service_id_or_device_id: int | str,
) -> InlineKeyboardMarkup:
    """Issues a secure single-use IPAuthToken and routes to the Shelter-style panel."""
    builder = InlineKeyboardBuilder()

    service_id = None
    if isinstance(service_id_or_device_id, int):
        service_id = service_id_or_device_id
    elif str(service_id_or_device_id).isdigit():
        service_id = int(service_id_or_device_id)

    if service_id:
        now = datetime.now(timezone.utc)
        raw_token = uuid.uuid4().hex

        # Invalidate old tokens & issue a fresh 10-minute token
        await session.execute(delete(IPAuthToken).where(IPAuthToken.service_id == service_id))
        session.add(
            IPAuthToken(
                token=raw_token,
                service_id=service_id,
                expires_at=now + timedelta(minutes=10),
                is_used=False,
            )
        )
        await session.commit()

        panel_url = f"{WEB_SERVER_BASE_URL}/ip/{raw_token}"
    else:
        panel_url = f"{WEB_SERVER_BASE_URL}/"

    builder.button(text="🌐 پنل مدیریت و ثبت آی‌پی 🌐", url=panel_url)
    builder.button(text="🤖 ثبت آی‌پی دستی (در ربات) 🤖", callback_data=f"manual_ip:{service_id_or_device_id}")
    if service_id:
        builder.button(text="🗺 تغییر سرور (لوکیشن)", callback_data=f"change_default_loc_select:{service_id}")

    app_settings = AppSettingsService(session)

    # Tutorial Video Link
    video_link = await app_settings.get_teaching_video_link()
    if video_link:
        clean_vid = video_link.strip()
        if clean_vid:
            if not clean_vid.startswith(("http://", "https://")):
                clean_vid = f"https://{clean_vid}"
            builder.row(InlineKeyboardButton(text="🎥 آموزش ویدیویی تنظیمات", url=clean_vid))

    # Support Link
    support_username = await app_settings.get_support_username()
    if support_username:
        builder.button(text="☎️ پشتیبانی آنلاین", url=f"https://t.me/{support_username.removeprefix('@')}")

    builder.adjust(1)
    return builder.as_markup()


# ============================================================================
# MY SERVICES PAGES & MANAGEMENT
# ============================================================================
async def _show_my_services_page(
    callback_or_message: CallbackQuery | Message,
    page: int,
    session: AsyncSession,
) -> None:
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
                    text="✳️ ثبت آی‌پی",
                    callback_data=f"manage_links:{service.id}",
                ),
                InlineKeyboardButton(
                    text="⚙️ تغییر لوکیشن",
                    callback_data=f"manage_service:{service.id}",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"🛒 تمدید اشتراک: {raw_name}",
                    callback_data="buy_back_to_plans",
                )
            )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"my_services_page:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"my_services_page:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="buy_back_to_menu"))

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

    service = await _get_owned_service_from_callback(callback, session, "manage_service", 2)
    if service is None:
        await _reject_invalid_service_callback(callback)
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

    service = await _get_owned_service_from_callback(callback, session, "manage_links", 2)
    if service is None:
        await _reject_invalid_service_callback(callback)
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

    text = await render_dns_delivery_text(
        session=session,
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

    service = await _get_owned_service_from_callback(callback, session, "manage_status", 2)
    if service is None:
        await _reject_invalid_service_callback(callback)
        return

    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")


# ============================================================================
# LOCATION SWITCHER (GERMANY, TURKEY, UAE)
# ============================================================================
async def _show_default_loc_page(
    callback: CallbackQuery,
    service_or_id: VPNService | int,
    page: int = 0,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
) -> None:
    if isinstance(service_or_id, int):
        if session is None:
            async with async_session_maker() as s:
                service = await ServicesRepository(s).get(service_or_id)
        else:
            service = await ServicesRepository(session).get(service_or_id)
    else:
        service = service_or_id

    if not service or not is_service_active(service):
        await callback.answer("❌ این اشتراک منقضی شده است یا یافت نشد.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    current_device = service.controld_device_id
    is_germany_active = current_device == SLOT_CONFIGS[1]["device_id"]
    is_turkey_active = current_device == SLOT_CONFIGS[5]["device_id"]
    is_uae_active = current_device == SLOT_CONFIGS[4]["device_id"]

    # Germany Button
    builder.button(
        text="🇩🇪 آلمان (فرانکفورت)" + (" (فعال)" if is_germany_active else ""),
        callback_data=f"apply_def_loc:{service.id}:1",
    )
    # Turkey Button
    builder.button(
        text="🇹🇷 ترکیه (استانبول)" + (" (فعال)" if is_turkey_active else ""),
        callback_data=f"apply_def_loc:{service.id}:5",
    )
    # Emirates Button
    builder.button(
        text="🇦🇪 امارات (دبی)" + (" (فعال)" if is_uae_active else ""),
        callback_data=f"apply_def_loc:{service.id}:4",
    )
    # Back Buttons
    builder.button(
        text="🔙 بازگشت به مشخصات سرویس",
        callback_data=f"manage_links:{service.id}",
    )
    builder.button(
        text="🏠 منوی اصلی",
        callback_data="buy_back_to_menu",
    )
    builder.adjust(1)

    active_name = (
        "🇩🇪 آلمان (فرانکفورت)"
        if is_germany_active
        else "🇹🇷 ترکیه (استانبول)"
        if is_turkey_active
        else "🇦🇪 امارات (دبی)"
        if is_uae_active
        else "سایر سرورها"
    )

    text = f"""🗺 <b>تغییر لوکیشن سرور دی‌ان‌اس</b>

👤 <b>نام دستگاه:</b> <code>{(service.username or '').split('|')[0]}</code>
📌 <b>سرور فعال شما:</b> <b>{active_name}</b>

کشوری که می‌خواهید ترافیک شما به سرور آن متصل شود را انتخاب کنید:"""

    if callback.message:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("change_default_loc_select:"), StateFilter("*"))
async def handle_change_default_loc_select(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)

    # 1. Ownership & Existence Verification
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if not user or not service or service.user_id != user.id:
        await callback.answer("⛔ شما دسترسی به این سرویس را ندارید.", show_alert=True)
        return

    # 2. Expiration Check
    if not is_service_active(service):
        await callback.answer("❌ این اشتراک منقضی شده است. امکان تغییر لوکیشن وجود ندارد.", show_alert=True)
        return

    await _show_default_loc_page(callback, service, settings=settings, session=session)


@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    slot_num = int(parts[2])

    service = await ServicesRepository(session).get(service_id)

    # 1. Ownership Verification
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if not user or not service or service.user_id != user.id:
        await callback.answer("⛔ شما دسترسی به این سرویس را ندارید.", show_alert=True)
        return

    # 2. Expiration & Slot Validation
    if not is_service_active(service):
        await callback.message.answer(
            "❌ <b>این اشتراک منقضی شده است.</b>\n\nامکان تغییر لوکیشن برای سرویس‌های منقضی شده وجود ندارد.",
            parse_mode="HTML",
        )
        return

    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات انتخاب‌شده نامعتبر است.")
        return

    new_device_id = SLOT_CONFIGS[slot_num]["device_id"]
    new_pop_name = SLOT_CONFIGS[slot_num]["name"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    # Check if already active on this slot
    if service.controld_device_id == new_device_id:
        await callback.message.answer(
            f"ℹ️ اشتراک شما در حال حاضر روی سرور <b>{escape(new_pop_name)}</b> فعال است.",
            parse_mode="HTML",
        )
        return

    await callback.message.edit_text(
        f"⚙️ <b>در حال انتقال سرور به {escape(new_pop_name)}...</b>\nلطفاً چند لحظه صبر کنید.",
        reply_markup=None,
        parse_mode="HTML",
    )

    controld = ControlDService(settings)
    old_device_id = service.controld_device_id
    user_ip = service.authorized_ip

    # 3. Provider Synchronization (Authorize new BEFORE deauthorizing old)
    if user_ip:
        try:
            logger.info("authorizing_new_slot", service_id=service.id, new_device=new_device_id, ip=user_ip)
            auth_ok = await controld.authorize_ip(new_device_id, user_ip)
            if not auth_ok:
                await callback.message.answer("❌ خطا در ثبت آی‌پی روی سرور جدید در کنترل‌دی. تغییر لوکیشن متوقف شد.")
                return
        except Exception as exc:
            logger.error("new_slot_ip_auth_failed", service_id=service.id, error=str(exc))
            await callback.message.answer("❌ خطای ارتباط با سرور کنترل‌دی. تغییر لوکیشن متوقف شد.")
            return

        # Deauthorize old slot only after successful authorization
        if old_device_id and old_device_id != new_device_id:
            try:
                logger.info("deauthorizing_old_slot", service_id=service.id, old_device=old_device_id, ip=user_ip)
                await controld.deauthorize_ip(old_device_id, user_ip)
            except Exception as exc:
                logger.warning("old_slot_deauth_failed_non_fatal", service_id=service.id, error=str(exc))

    # 4. Atomic Database Commit
    raw_username = service.username.split("|")[0].strip() if service.username else "دستگاه"
    service.username = f"{raw_username}|default|{slot_num}"
    service.controld_device_id = new_device_id
    await session.commit()

    # 5. Success Message with New Dedicated DNS IPs
    success_text = f"""✅ <b>لوکیشن سرور شما با موفقیت تغییر یافت!</b>

📍 <b>سرور فعال جدید:</b> <b>{escape(new_pop_name)}</b>
👤 <b>نام دستگاه:</b> <code>{escape(raw_username)}</code>
━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>توجه بسیار مهم (تغییر آدرس‌های DNS):</b>
با تغییر لوکیشن، آدرس‌های سرور DNS اختصاصی شما تغییر کرده‌اند.
<b>حتماً آدرس‌های جدید زیر را در تنظیمات کنسول، کامپیوتر یا مودم خود جایگزین DNS قبلی نمایید:</b>

🔹 <b>Primary DNS:</b> <code>{ipv4_primary}</code>
🔹 <b>Secondary DNS:</b> <code>{ipv4_secondary}</code>
━━━━━━━━━━━━━━━━━━━━━
📋 <b>مراحل نهایی:</b>
1️⃣ آدرس‌های DNS جدید را روی دستگاه خود ست و ذخیره کنید.
2️⃣ فیلترشکن را خاموش نمایید.
3️⃣ روی دکمه <b>«🌐 پنل مدیریت و ثبت آی‌پی»</b> زیر کلیک کنید تا از اتصال مطمئن شوید.
"""

    markup = await create_secure_ip_update_keyboard(session, service.id)
    sent_msg = await callback.message.answer(text=success_text, reply_markup=markup, parse_mode="HTML")
    await schedule_message_deletion(callback.bot, sent_msg.chat.id, sent_msg.message_id, delay_seconds=7200)

# ============================================================================
# MANUAL IP REGISTRATION (IN-BOT FLOW)
# ============================================================================
@router.callback_query(F.data.startswith("manual_ip:"))
@router.callback_query(F.data.startswith("manual_ip_reg:"))
async def on_manual_ip_clicked(call: CallbackQuery, state: FSMContext) -> None:
    data_val = call.data.split(":")[1]
    await state.update_data(target_ref=data_val)

    prompt_text = """🤖 <b>ثبت دستی آدرس آی‌پی (IPv4)</b>

اگر به هر دلیلی مایل به باز کردن لینک وب‌سایت نیستید، می‌توانید آی‌پی عمومی اینترنت ایران خود را مستقیماً ارسال نمایید.

📋 <b>مراحل دریافت و ارسال آی‌پی:</b>
1️⃣ <b>فیلترشکن و پروکسی تلگرام خود را کاملاً خاموش کنید.</b>
2️⃣ وارد یکی از سایت‌های زیر شوید تا آی‌پی واقعی شما نمایش داده شود:
🌐 <a href="https://ipnumberia.com">ipnumberia.com</a>
🌐 <a href="https://api.ipify.org">api.ipify.org</a>

3️⃣ آی‌پی عددی نمایش‌داده‌شده را کپی کرده و همین‌جا بفرستید.

<i>📌 نمونه فرمت صحیح:</i> <code>5.200.12.1</code>
❌ برای انصراف: /cancel"""

    await call.message.answer(
        prompt_text,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await state.set_state(ManualIPState.waiting_for_ip)
    await call.answer()


@router.message(ManualIPState.waiting_for_ip, F.text)
async def handle_manual_ip_submission(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw_ip = (message.text or "").strip()

    if raw_ip.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ عملیات ثبت دستی آی‌پی لغو شد.")
        return

    # Check for valid IPv4 structure and private/loopback addresses
    try:
        ip_obj = ipaddress.IPv4Address(raw_ip)
        if ip_obj.is_private or ip_obj.is_loopback:
            await message.answer("❌ این آی‌پی عمومی نیست (Private/Local). لطفاً آی‌پی اصلی اینترنت خود را وارد کنید.")
            return
    except ValueError:
        await message.answer("❌ فرمت آی‌پی نامعتبر است. لطفاً فقط ساختار عددی مانند <code>5.200.10.15</code> را ارسال کنید:", parse_mode="HTML")
        return

    # Anti-VPN Verification
    ip_check = await verify_user_ip(raw_ip)
    if not ip_check.is_iran:
        await message.answer(
            f"⚠️ <b>خطا: فیلترشکن شما روشن است یا آی‌پی غیرایرانی وارد شده!</b>\n\n"
            f"🌐 آی‌پی: <code>{escape(raw_ip)}</code>\n"
            f"🗺 کشور: {escape(ip_check.country)} ({escape(ip_check.country_code)})\n\n"
            "❌ ثبت آی‌پی فقط برای اینترنت مستقیم ایران مجاز است. لطفاً فیلترشکن را خاموش کرده و آی‌پی واقعی خود را ارسال فرمایید.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    target_ref = data.get("target_ref")
    await state.clear()

    # Look up service
    service = None
    if str(target_ref).isdigit():
        service = await session.get(VPNService, int(target_ref))

    if not service:
        # Fallback to user's latest active service
        user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
        if user:
            stmt = (
                select(VPNService)
                .where(VPNService.user_id == user.id, VPNService.status == "active")
                .order_by(VPNService.expire_at.desc())
                .limit(1)
            )
            res = await session.execute(stmt)
            service = res.scalars().first()

    if not service:
        await message.answer("❌ سرویس فعالی برای اعمال این آی‌پی در سیستم یافت نشد.")
        return

    wait_msg = await message.answer("⏳ در حال ثبت و همگام‌سازی آی‌پی با سرورها...")

    success = await update_device_ip_safe(session, service, raw_ip)

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if success:
        await message.answer(f"✅ آی‌پی شما با موفقیت ثبت و فعال شد!\n\n🌐 آی‌پی فعال: <code>{raw_ip}</code>", parse_mode="HTML")
    else:
        await message.answer("❌ خطا در همگام‌سازی با سرورها. لطفاً دقایقی دیگر تلاش کنید یا با پشتیبانی تماس بگیرید.")