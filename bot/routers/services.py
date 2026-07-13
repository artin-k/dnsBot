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

router = Router(name="services")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url


def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
    """Generates direct update-ip links to bypass capture-ip entirely [cite: controld_buy.py, run_web_ip_updater.py]."""
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
        slot_num_str = parts[2] if len(parts) > 2 else "1"
        
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
            # Side-by-Side: Quick IP Register | Management Panel [cite: services.py]
            builder.row(
                InlineKeyboardButton(
                    text=f"✳️ ثبت آی‌پی سریع",
                    url=f"{WEB_SERVER_BASE_URL}/update-ip/{service.controld_device_id}"  # Corrected to use device_id string [cite: admin.py]
                ),
                InlineKeyboardButton(
                    text=f"🛠 مدیریت",
                    callback_data=f"manage_service:{service.id}"
                )
            )
        else:
            # Full-width warning button for expired plans
            builder.row(
                InlineKeyboardButton(
                    text=f"❌ منقضی شده: {raw_name}",
                    callback_data=f"manage_service:{service.id}"
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


# ============================================================================
# NEW ROBUST STRING-BASED SERVICE CONTROLLERS (REPLACES EXPIRED/BROKEN CLASSES)
# ============================================================================

@router.callback_query(F.data.startswith("manage_service:"), StateFilter("*"))
async def handle_manage_service(callback: CallbackQuery, session: AsyncSession) -> None:
    """Displays the administrative options for the subscription [cite: services.py, 1]."""
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


@router.callback_query(F.data.startswith("manage_links:"), StateFilter("*"))
async def handle_manage_links(callback: CallbackQuery, session: AsyncSession) -> None:
    """Generates the connection links and update-ip buttons directly [cite: services.py, 1]."""
    await callback.answer()
    if callback.message is None:
        return
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    text = f"""🔗 لینک‌های اتصال سرویس <code>{escape(service.username.split("|")[0])}</code>

<b>لینک اشتراک DoT:</b>
<code>{escape(service.subscription_link or "ثبت نشده")}</code>

<b>لینک کانفیگ DoH:</b>
<code>{escape(service.config_link or "ثبت نشده")}</code>"""
    
    markup = await create_secure_ip_update_keyboard(session, service.id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


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
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"manage_service:{service_id}"))

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

# --- LOCATE AND REPLACE THE handle_apply_def_loc HANDLER ---
@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """
    Dynamically migrates a user's IP registration from an old static slot to a new one statically.
    1. Deauthorizes the OLD IP from the OLD slot (DELETE /access) [cite: 1].
    2. Authorizes the OLD IP on the NEW slot (POST /access) [cite: 1].
    3. Updates the database record and commits atomically [cite: 1].
    """
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    slot_num = int(parts[2])

    # 1. Retrieve the service details
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس یافت نشد.")
        return

    from app.config import SLOT_CONFIGS
    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات انتخاب شده معتبر نیست.")
        return

    new_device_id = SLOT_CONFIGS[slot_num]["device_id"]
    new_pop_name = SLOT_CONFIGS[slot_num]["name"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    # Avoid redundant migrations
    if service.controld_device_id == new_device_id:
        await callback.message.answer(f"ℹ️ اشتراک شما در حال حاضر روی سرور {new_pop_name} فعال است.")
        return

    # Map the old slot name for a friendly confirmation message
    old_device_id = service.controld_device_id
    old_location_name = "سرور قبلی"
    for config in SLOT_CONFIGS.values():
        if config["device_id"] == old_device_id:
            old_location_name = config["name"]
            break

    # Disable the inline keyboard to prevent double clicks
    await callback.message.edit_text(
        f"⚙️ در حال انتقال لوکیشن اشتراک شما به سرور {new_pop_name}...",
        reply_markup=None
    )

    controld = ControlDService(settings)
    user_ip = service.authorized_ip

    # Step 2: Surgically deauthorize the user's IP from the old slot (DELETE) [cite: 1]
    if old_device_id and user_ip:
        try:
            logger.info("surgically_deauthorizing_old_slot_ip", service_id=service.id, old_device_id=old_device_id, ip=user_ip)
            await controld.deauthorize_ip(old_device_id, user_ip)
        except Exception as exc:
            # Log warning but do NOT block provisioning of the new slot
            logger.warning("old_slot_ip_deauthorization_failed_proceeding", service_id=service.id, error=str(exc))

    # Step 3: Authorize the user's IP on the new slot (POST) [cite: 1]
    if user_ip:
        try:
            logger.info("authorizing_ip_on_new_slot", service_id=service.id, new_device_id=new_device_id, ip=user_ip)
            await controld.authorize_ip(new_device_id, user_ip)
        except Exception as exc:
            logger.error("new_slot_ip_authorization_failed", service_id=service.id, error=str(exc))

    # Step 4: Update database and commit atomically [cite: 1]
    try:
        # Reconstruct the metadata string
        raw_username = service.username.split("|")[0]
        service.username = f"{raw_username}|default|{slot_num}"
        service.controld_device_id = new_device_id
        
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_commit_location_switch_db_transaction", service_id=service.id, error=str(exc))
        await callback.message.answer("❌ خطای پایگاه داده در ذخیره‌سازی لوکیشن جدید. تغییرات لغو شد.")
        return

    # Step 5: Send Success Telegram Message
    success_text = f"""✅ <b>لوکیشن اشتراک شما با موفقیت تغییر یافت!</b>

🔄 <b>سرور قبلی:</b> {escape(old_location_name)}
🗺 <b>سرور جدید:</b> {escape(new_pop_name)}

🔐 <b>دی‌ان‌اس‌های اختصاصی سرور جدید:</b>
Primary: <code>{ipv4_primary}</code>
Secondary: <code>{ipv4_secondary}</code>

⚠️ <i>در صورت عدم اتصال، لطفاً مجدداً روی دکمه «ثبت آی‌پی اتوماتیک» زیر کلیک کنید.</i>"""

    # Generate the direct /update-ip/{device_id} keyboard cleanly
    from bot.routers.services import _get_ip_registration_keyboard
    markup = _get_ip_registration_keyboard(new_device_id)

    await callback.message.answer(
        success_text,
        reply_markup=markup,
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


# ============================================================================
# RELIABLE DIRECT UPDATE-IP KEYBOARD GENERATOR
# ============================================================================

# bot/routers/services.py

async def create_secure_ip_update_keyboard(session: AsyncSession, service_id: int) -> InlineKeyboardMarkup:
    """
    Unified keyboard generator mapping directly to the operational /update-ip route.
    Bypasses unstable database secure links entirely [cite: controld_buy.py, run_web_ip_updater.py].
    """
    service = await session.get(VPNService, service_id)
    device_id = service.controld_device_id if service else "unknown"
    return _get_ip_registration_keyboard(device_id)