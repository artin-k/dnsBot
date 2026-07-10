# bot/routers/services.py
from html import escape
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import httpx
import structlog

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import Settings, get_settings, SLOT_CONFIGS
from app.models import Plan, VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService
from app.utils.formatting import format_datetime
from bot import menu_actions
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.services import ServiceActionCallback

router = Router(name="services")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url


def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
    """Generates the automatic and manual IP registration controls [cite: buy.py]."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
    builder.adjust(1)
    return builder.as_markup()


def format_service_item_display(service: VPNService, index: int) -> str:
    """
    Parses active subscription metadata cleanly to display beautiful 
    Persian flag tags and localized server names [cite: services.py].
    """
    raw_username = service.username or ""
    service_display = "کل ترافیک اینترنت (Default)"
    country_display = "پیش‌فرض"
    username_part = raw_username
    
    if "|" in raw_username:
        parts = raw_username.split("|")
        username_part = parts[0]
        service_pk = parts[1] if len(parts) > 1 else "default"
        slot_num_str = parts[2] if len(parts) > 2 else None
        
        # Format game/service display name
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
                
        # Resolve static slot display name [cite: services.py, 1]
        if slot_num_str and slot_num_str.isdigit():
            slot_num = int(slot_num_str)
            if slot_num in SLOT_CONFIGS:
                country_display = SLOT_CONFIGS[slot_num]["name"]
    
    status_fa = "🟢 فعال" if service.status == "active" else "🔴 منقضی شده"
    
    return f"""<b>{index}. 👤 نام دستگاه:</b> <code>{escape(username_part)}</code>
🎮 <b>برنامه/بازی:</b> {escape(service_display)}
🗺 <b>سرور (کشور):</b> {escape(country_display)}
⚡ <b>پلن:</b> {escape(service.plan.title if service.plan else "اکانت تست")}
🗓 <b>تاریخ انقضا:</b> {format_datetime(service.expire_at)}
📌 <b>وضعیت:</b> {status_fa}
"""


def _get_service_manage_keyboard(service_id: int, device_id: str | None = None) -> InlineKeyboardMarkup:
    """Generates active service management keyboard with side-by-side quick action pairings [cite: services.py]."""
    builder = InlineKeyboardBuilder()
    
    # Add quick IP registration actions directly in management panel [cite: buy.py]
    if device_id:
        builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
        builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
        
    builder.button(
        text="🔗 لینک‌های اتصال",
        callback_data=ServiceActionCallback(action="link", service_id=service_id)
    )
    builder.button(
        text="📊 وضعیت سرویس",
        callback_data=ServiceActionCallback(action="status", service_id=service_id)
    )
    builder.button(
        # Rerouted to go directly to our static 5-slot location changer [cite: services.py, 1]
        text="🗺 تنظیمات لوکیشن سرور",
        callback_data=f"change_default_loc_select:{service_id}"
    )
    
    if device_id:
        builder.adjust(1, 1, 1, 1, 1)
    else:
        builder.adjust(1)
        
    return builder.as_markup()


async def _show_my_services_page(callback_or_message: CallbackQuery | Message, page: int, session: AsyncSession) -> None:
    """Renders 3 parsed services per page dynamically with dynamic side-by-side quick actions [cite: services.py, 1]."""
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

    # Sort services: Active first, then Expired, then by expiration date descending [cite: services.py]
    services.sort(key=lambda s: (0 if s.status == "active" else 1, s.expire_at), reverse=True)

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
        
        if service.status == "active":
            # UX Masterpiece: Generate side-by-side quick buttons for active plans [cite: services.py]
            builder.row(
                InlineKeyboardButton(
                    text=f"✳️ ثبت آی‌پی سریع",
                    url=f"{WEB_SERVER_BASE_URL}/update-ip/{service.controld_device_id}"
                ),
                InlineKeyboardButton(
                    text=f"🛠 مدیریت",
                    callback_data=ServiceActionCallback(action="status", service_id=service.id).pack()
                )
            )
        else:
            # Full-width warning button for expired plans
            builder.row(
                InlineKeyboardButton(
                    text=f"❌ منقضی شده: {raw_name}",
                    callback_data=ServiceActionCallback(action="status", service_id=service.id).pack()
                )
            )

    # Append standard page controls at the bottom
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


@router.callback_query(ServiceActionCallback.filter(F.action.in_({"link", "status", "renew"})))
async def service_action(
    callback: CallbackQuery,
    callback_data: ServiceActionCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_answer(callback, "ابتدا /start را ارسال کنید.")
        return

    service = await ServicesRepository(session).get_user_service(callback_data.service_id, user.id)
    if service is None:
        await _safe_answer(callback, "این سرویس پیدا نشد یا متعلق به حساب شما نیست.")
        return

    if callback_data.action == "renew":
        await _safe_answer(
            callback,
            "♻️ تمدید مستقیم سرویس در حال حاضر فعال نیست.\n\nبرای تمدید، لطفاً از بخش «خرید اشتراک» همان پلن را مجدداً خریداری کنید تا زمان آن به این سرویس افزوده شود.",
        )
        return

    if callback_data.action == "link":
        text = f"""🔗 لینک‌های سرویس <code>{escape(service.username.split("|")[0])}</code>

<b>لینک اشتراک DoT:</b>
<code>{escape(service.subscription_link or "ثبت نشده")}</code>

<b>لینک کانگیف DoH:</b>
<code>{escape(service.config_link or "ثبت نشده")}</code>"""
        
        await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id, service.controld_device_id), parse_mode="HTML")
        return

    # Default action: status
    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id, service.controld_device_id), parse_mode="HTML")


# ============================================================================
# CATCH-ALL STATIC LOCATION SWITCHER
# ============================================================================

async def _show_default_loc_page(callback: CallbackQuery, service_id: int, page: int, settings: Settings) -> None:
    """Renders the location selection menu displaying exactly your 5 fixed premium slots [cite: 1]."""
    builder = InlineKeyboardBuilder()
    for slot_num, config in SLOT_CONFIGS.items():
        builder.button(
            text=config["name"],
            callback_data=f"apply_def_loc:{service_id}:{slot_num}" # Pass the Slot Number!
        )
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=ServiceActionCallback(action="status", service_id=service_id).pack()))

    await callback.message.edit_text(
        "🗺 <b>تغییر لوکیشن سرور دی‌ان‌اس</b>\n\n"
        "کشوری که می‌خواهید ترافیک اینترنت شما به سرور آن متصل شود را انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("change_default_loc_select:"), StateFilter("*"))
async def handle_change_default_loc_select(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    service_id = int(callback.data.split(":")[1])
    await _show_default_loc_page(callback, service_id, page=0, settings=settings)


# bot/routers/services.py

# --- LOCATE AND REPLACE THE handle_apply_def_loc METHOD ---
@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Migrates a user's IP registration from one static slot to another, wiping the old one [cite: services.py, 1]."""
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

    from app.config import SLOT_CONFIGS
    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات نامعتبر است.")
        return

    new_device_id = SLOT_CONFIGS[slot_num]["device_id"]
    new_pop_name = SLOT_CONFIGS[slot_num]["name"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    # Prevent redundant migrations
    if service.controld_device_id == new_device_id:
        await callback.message.answer(f"ℹ️ اشتراک شما در حال حاضر روی سرور {new_pop_name} فعال است.")
        return

    await callback.message.edit_text(f"⚙️ در حال انتقال لوکیشن اشتراک شما به سرور {new_pop_name}...")

    # Step 1: Self-Cleaning: Fetch and deauthorize ALL active IPs from their OLD permanent slot [cite: 1]
    controld = ControlDService(settings)
    if service.controld_device_id:
        try:
            active_ips = await controld.get_active_ips(service.controld_device_id)
            logger.info("fetched_active_ips_for_migration_cleanup", device_id=service.controld_device_id, ips=active_ips)
            for active_ip in active_ips:
                await controld.deauthorize_ip(service.controld_device_id, active_ip)
        except Exception as exc:
            logger.error("failed_to_clean_old_slot_ips_during_migration", device_id=service.controld_device_id, error=str(exc))

    # Step 2: Authorize their IP on their NEW permanent slot [cite: 1]
    if service.authorized_ip:
        logger.info("authorizing_new_slot", service_id=service.id, new_device_id=new_device_id, ip=service.authorized_ip)
        await controld.authorize_ip(new_device_id, service.authorized_ip)

    # Step 3: Update local DB record with new slot assignment and metadata [cite: services.py, 1]
    raw_username = service.username.split("|")[0]
    service.username = f"{raw_username}|default|{slot_num}"
    service.controld_device_id = new_device_id
    await session.commit()

    success_text = f"""✅ <b>لوکیشن اشتراک شما با موفقیت تغییر یافت!</b>

🗺 <b>سرور جدید:</b> {escape(new_pop_name)}

🔐 <b>دی‌ان‌اس‌های اختصاصی سرور جدید:</b>
Primary: <code>{ipv4_primary}</code>
Secondary: <code>{ipv4_secondary}</code>

⚠️ <i>در صورت عدم اتصال، لطفاً مجدداً روی دکمه «ثبت آی‌پی اتوماتیک» زیر کلیک کنید.</i>"""

    await callback.message.answer(
        success_text,
        reply_markup=_get_ip_registration_keyboard(new_device_id),
        parse_mode="HTML"
    )


async def _safe_answer(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)


@router.callback_query(F.data.startswith("def_loc_page:"), StateFilter("*"))
async def handle_def_loc_page(callback: CallbackQuery, settings: Settings) -> None:
    """Handles pagination buttons for the default internet traffic location changer."""
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    page = int(parts[2])

    await _show_default_loc_page(callback, service_id, page, settings)