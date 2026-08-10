# bot/routers/services.py
from html import escape
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import jdatetime
import httpx
import structlog

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.config import Settings, get_settings, SLOT_CONFIGS
from app.models import Plan, VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService
from app.utils.formatting import format_datetime
from bot import menu_actions
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
import uuid
# bot/routers/services.py
from app.services.settings_service import AppSettingsService
from bot.utils.auto_clean import schedule_message_deletion

from app.models import IPAuthToken

router = Router(name="services")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url


# bot/routers/services.py

def is_service_active(service: VPNService) -> bool:
    """
    Determines if a service is active.
    A service is ACTIVE if its status is not 'disabled' and its expire_at date is in the future.
    """
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
    """
    Parses active subscription metadata cleanly to display localized server names and real-time status.
    """
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
            try:
                from bot.routers.buy import CATEGORIES
                for cat in CATEGORIES.values():
                    for s in cat["services"]:
                        if s["pk"] == service_pk:
                            service_display = s["name"]
                            break
            except Exception:
                service_display = service_pk.capitalize()
                
        if slot_num_str and slot_num_str.isdigit():
            slot_num = int(slot_num_str)
            if slot_num in SLOT_CONFIGS:
                country_display = SLOT_CONFIGS[slot_num]["name"]
    
    # Dynamic active calculation
    active = is_service_active(service)
    status_fa = "🟢 فعال" if active else "🔴 منقضی شده"
    
    return f"""<b>{index}. 👤 نام دستگاه:</b> <code>{escape(username_part)}</code>
🎮 <b>برنامه/بازی:</b> {escape(service_display)}
🗺 <b>سرور (کشور):</b> {escape(country_display)}
⚡ <b>پلن:</b> {escape(service.plan.title if service.plan else "اکانت تست")}
🗓 <b>تاریخ انقضا:</b> {format_datetime(service.expire_at)}
📌 <b>وضعیت:</b> {status_fa}
"""


def calculate_remaining_time_fa(expire_at: datetime | None) -> str:
    """Dynamically calculates remaining days/hours from expire_at."""
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
    total_minutes = int(total_seconds // 60)
    return f"{total_minutes} دقیقه"


def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
    """Generates direct update-ip links to bypass capture-ip entirely."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
    builder.adjust(1)
    return builder.as_markup()


def format_service_item_display(service: VPNService, index: int) -> str:
    """
    Parses active subscription metadata cleanly to display beautiful 
    Persian flag tags and localized server names.
    """
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
            try:
                from bot.routers.buy import CATEGORIES
                for cat in CATEGORIES.values():
                    for s in cat["services"]:
                        if s["pk"] == service_pk:
                            service_display = s["name"]
                            break
            except Exception:
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
    """Generates active service management keyboard using secure string callbacks."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔗 لینک‌های اتصال",
        callback_data=f"manage_links:{service_id}"
    )
    builder.button(
        text="📊 وضعیت سرویس",
        callback_data=f"manage_status:{service_id}"
    )
    builder.button(
        text="🗺 تنظیمات لوکیشن سرور",
        callback_data=f"change_default_loc_select:{service_id}"
    )
    builder.button(
        text="🔙 بازگشت به لیست",
        callback_data="my_services_page:0"
    )
    builder.adjust(1)
    return builder.as_markup()


# bot/routers/services.py

async def _show_my_services_page(callback_or_message: CallbackQuery | Message, page: int, session: AsyncSession) -> None:
    """Renders parsed services per page with newest active subscriptions on top."""
    user_id = callback_or_message.from_user.id
    user = await UsersRepository(session).get_by_telegram_id(user_id)
    if not user:
        return

    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        msg = "شما هنوز هیچ سرویس یا اشتراکی تهیه نکرده‌اید."
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.answer(msg)
        else:
            await callback_or_message.answer(msg)
        return

    # Sort Active subscriptions FIRST (0), Expired LAST (1), ordered by newest expire_at date
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
                    url=f"{WEB_SERVER_BASE_URL}/update-ip/{service.controld_device_id}"
                ),
                InlineKeyboardButton(
                    text="🛠 مدیریت",
                    callback_data=f"manage_service:{service.id}"
                )
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=f"🛒 خرید / تمدید: {raw_name}",
                    callback_data="buy_back_to_plans"
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
    
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await callback_or_message.answer(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.message(F.text == texts.BTN_MY_SERVICES)
async def my_services(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _show_my_services_page(message, page=0, session=session)


@router.callback_query(F.data.startswith("my_services_page:"), StateFilter("*"))
async def handle_my_services_page(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await _show_my_services_page(callback, page, session)


@router.callback_query(F.data.startswith("manage_service:"), StateFilter("*"))
async def handle_manage_service(callback: CallbackQuery, session: AsyncSession) -> None:
    """Displays the administrative options for the subscription."""
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
            parse_mode="HTML"
        )
        return

    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("manage_links:"), StateFilter("*"))
async def handle_manage_links(callback: CallbackQuery, session: AsyncSession) -> None:
    """Generates the connection links and update-ip buttons directly."""
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
            try:
                from bot.routers.buy import CATEGORIES
                for cat in CATEGORIES.values():
                    for s in cat["services"]:
                        if s["pk"] == service_pk:
                            service_display = s["name"]
                            break
            except Exception:
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

    expire_at = service.expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    tehran_tz = ZoneInfo("Asia/Tehran")
    tehran_expire = expire_at.astimezone(tehran_tz)
    try:
        naive_tehran = tehran_expire.replace(tzinfo=None)
        expire_str = jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        expire_str = tehran_expire.strftime("%Y-%m-%d %H:%M:%S")

    remaining_time = calculate_remaining_time_fa(service.expire_at)

    text = f"""📊 <b>لینک‌های اتصال سرویس {escape(device_name)}</b>

🔹 <b>تاریخ انقضاء پلن :</b> <code>{escape(expire_str)}</code>
🔷 <b>زمان باقی‌مانده:</b> {escape(remaining_time)}
🎮 <b>برنامه/بازی:</b> <code>{escape(service_display)}</code>
🗺 <b>سرور (کشور) فعلی:</b> {escape(country_name)}

🔐 <b>دی‌ان‌اس‌های اختصاصی شما:</b>
Primary: <code>{escape(ipv4_primary)}</code>
Secondary: <code>{escape(ipv4_secondary)}</code>

📱 <b>دی‌ان‌اس‌های مخصوص موبایل شما:</b>
🔷 <code>76.76.2.22</code>

مراحل ثبت آی‌پی (بسیار مهم):
1️⃣ : دستگاه خود (موبایل یا لپ‌تاپ) را به همان مودم/روتری وصل کنید که کنسول یا سیستم بازی شما به آن متصل است.
2️⃣ : فیلترشکن و پروکسی تلگرام خود را خاموش کرده و مجدد روی دکمه ثبت آیپی زیر کلیک کنید تا آی‌پی مودم شما ثبت شود.
❌ در صورت عدم ثبت آی‌پی روی مودم/روتر مشترک، دی‌ان‌اس‌ها متصل نخواهند شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید."""   
     
    markup = await create_secure_ip_update_keyboard(session, service.id)

    # If updating an existing message:
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    # Schedule deletion for this edited message
    await schedule_message_deletion(callback.bot, callback.message.chat.id, callback.message.message_id, delay_seconds=7200)


@router.callback_query(F.data.startswith("manage_status:"), StateFilter("*"))
async def handle_manage_status(callback: CallbackQuery, session: AsyncSession) -> None:
    """Displays the live connection status of the service."""
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


async def _show_default_loc_page(callback: CallbackQuery, service_id: int, page: int, settings: Settings) -> None:
    """Renders the location selection menu displaying exactly your 5 fixed premium slots."""
    builder = InlineKeyboardBuilder()
    for slot_num, config in SLOT_CONFIGS.items():
        builder.button(
            text=config["name"],
            callback_data=f"apply_def_loc:{service_id}:{slot_num}"
        )
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"manage_service:{service_id}"))

    await callback.message.edit_text(
        "🗺 <b>تغییر لوکیشن سرور دی‌ان‌اس</b>\n\n"
        "کشوری که می‌خواهید ترافیک اینترنت شما به سرور آن متصل شود را انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("change_default_loc_select:"), StateFilter("*"))
async def handle_change_default_loc_select(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)

    if not service or not is_service_active(service):
        await callback.answer("❌ این اشتراک منقضی شده است. امکان تغییر لوکیشن وجود ندارد.", show_alert=True)
        return

    await _show_default_loc_page(callback, service_id, page=0, settings=settings)


# bot/routers/services.py

@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Migrates a user's IP registration from one static slot to another statically."""
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
            parse_mode="HTML"
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
        reply_markup=None
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

    raw_username = service.username.split("|")[0]
    service.username = f"{raw_username}|default|{slot_num}"
    service.controld_device_id = new_device_id
    await session.commit()

  # Clean, professional HTML formatted string
    success_text = (
        f"✅ <b>لوکیشن اشتراک با موفقیت تغییر یافت!</b>\n\n"
        f"🗺 <b>سرور جدید:</b> {escape(new_pop_name)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 <b>دی‌ان‌اس‌های اختصاصی سرور جدید:</b>\n"
        f"🔷 Primary: <code>{escape(ipv4_primary)}</code>\n"
        f"🔷 Secondary: <code>{escape(ipv4_secondary)}</code>\n\n"
        f"📱 <b>دی‌ان‌اس مخصوص موبایل:</b>\n"
        f"🔷 Primary: <code>76.76.2.22</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>مراحل فعال‌سازی (بسیار مهم):</b>\n\n"
        f"1️⃣ دی‌ان‌AS‌های جدید بالا را جایگزین DNS قبلی دستگاه خود کنید.\n"
        f"2️⃣ موبایل و کنسول/سیستم خود را به یک اینترنت مشترک وصل کنید.\n"
        f"3️⃣ فیلترشکن و پروکسی تلگرام و پروکسی تلگرام خود را خاموش کنید.\n"
        f"4️⃣ مجدد روی دکمه ثبت آیپی زیر کلیک کنید.\n\n"
        f"💡 <i>نکته: شما می‌توانید لوکیشن DNS را بعد از خرید به تعداد نامحدود تغییر دهید.</i>\n\n"
        f"⚠️ <i>اگر پس از تغییر DNS اتصال برقرار نشد، یک بار مجدد روی دکمه ثبت آیپی زیر کلیک کنید و مجدداً وضعیت اتصال را بررسی کنید.</i>"
    )

    markup = await create_secure_ip_update_keyboard(session, service.id)

    # 1. Capture sent message
    sent_msg = await callback.message.answer(
        text=success_text,
        reply_markup=markup,
        parse_mode="HTML"
    )

    # 2. Schedule deletion after 2 hours (7200s)
    await schedule_message_deletion(callback.bot, sent_msg.chat.id, sent_msg.message_id, delay_seconds=7200)


@router.callback_query(F.data.startswith("def_loc_page:"), StateFilter("*"))
async def handle_def_loc_page(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    page = int(parts[2])

    await _show_default_loc_page(callback, service_id, page, settings)


async def create_secure_ip_update_keyboard(session: AsyncSession, service_id: int) -> InlineKeyboardMarkup:
    """Unified keyboard generator mapping directly to the operational /update-ip route."""
    service = await session.get(VPNService, service_id)
    device_id = service.controld_device_id if service else "unknown"
    return _get_ip_registration_keyboard(device_id)
# bot/routers/services.py

# bot/routers/services.py

def build_ip_registration_keyboard(
    device_id: str,
    support_username: str = "",
    video_link: str = ""
) -> InlineKeyboardMarkup:
    """
    Builds the full IP registration inline keyboard with:
    1. Automatic IP 1
    2. Automatic IP 2
    3. Manual IP
    4. Online Support (Direct to Admin PV)
    5. Teaching Video
    """
    builder = InlineKeyboardBuilder()
    
    # 1. Sanitize base URL
    base_url = WEB_SERVER_BASE_URL.strip().rstrip('/')
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
        
    dev_str = str(device_id or "unknown").strip()
    update_url = f"{base_url}/update-ip/{dev_str}"

    # Primary registration buttons
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=update_url)
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=update_url)
    builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{dev_str}")

    # 2. Add Online Support Button (Direct redirect to Admin PV)
    if support_username:
        clean_sup = support_username.removeprefix("@").strip()
        if clean_sup:
            builder.button(text="☎️ پشتیبانی آنلاین", url=f"https://t.me/{clean_sup}")

    # 3. Add Teaching Video Button
    if video_link:
        clean_vid = video_link.strip()
        if clean_vid:
            if not clean_vid.startswith(("http://", "https://")):
                clean_vid = f"https://{clean_vid}"
            builder.button(text="🎥 ویدیو آموزشی", url=clean_vid)

    # Stack all buttons cleanly in full-width single columns
    builder.adjust(1)
    return builder.as_markup()


async def create_secure_ip_update_keyboard(session: AsyncSession, service_id_or_device_id: int | str) -> InlineKeyboardMarkup:
    """
    Unified keyboard generator fetching dynamic settings (Support ID & Video Link).
    Includes an automatic fallback to the Root Admin's Telegram username.
    """
    if isinstance(service_id_or_device_id, int):
        service = await session.get(VPNService, service_id_or_device_id)
        device_id = service.controld_device_id if service else "unknown"
    else:
        device_id = str(service_id_or_device_id)

    app_settings = AppSettingsService(session)
    support_username = await app_settings.get_support_username()
    video_link = await app_settings.get_teaching_video_link()

    # Fallback to root admin username if support ID is not set in DB
    if not support_username:
        settings = get_settings()
        if settings.root_admin_telegram_id:
            from app.repositories.users import UsersRepository
            root_user = await UsersRepository(session).get_by_telegram_id(settings.root_admin_telegram_id)
            if root_user and root_user.telegram_username:
                support_username = root_user.telegram_username

    return build_ip_registration_keyboard(
        device_id=device_id,
        support_username=support_username,
        video_link=video_link,
    )

async def create_secure_ip_update_keyboard(session: AsyncSession, service_id_or_device_id: int | str) -> InlineKeyboardMarkup:
    """
    Unified keyboard generator fetching dynamic settings (Support ID & Video Link).
    """
    if isinstance(service_id_or_device_id, int):
        service = await session.get(VPNService, service_id_or_device_id)
        device_id = service.controld_device_id if service else "unknown"
    else:
        device_id = str(service_id_or_device_id)

    app_settings = AppSettingsService(session)
    support_username = await app_settings.get_support_username()
    video_link = await app_settings.get_teaching_video_link()

    return build_ip_registration_keyboard(
        device_id=device_id,
        support_username=support_username,
        video_link=video_link,
    )