# bot/routers/buy.py
from __future__ import annotations

import os
import re
import secrets
import httpx
import structlog
from datetime import datetime, timezone, timedelta
from html import escape
import jdatetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Payment, PaymentStatus, Plan, VPNService, VPNServiceStatus, OrderKind, OrderStatus, DiceRoll
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.repositories.payments import PaymentsRepository
from app.services.order_service import OrderService
from app.services.payment_service import (
    InsufficientWalletBalanceError,
    PaymentAlreadyProcessedError,
    PaymentApprovalError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.paystar import PaystarService
from app.services.settings_service import AppSettingsService
from app.services.username_validator import validate_username
from app.services.vpn_panel import VPNPanelService
from app.services.controld import ControlDService, get_category_label_fa
from app.services.slot_manager import get_least_populated_personal_slot
from app.services.ip_manager import update_device_ip_safe
from app.utils.formatting import format_money
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.buy import PlanCallback, paystar_payment_keyboard
from bot.routers.services import create_secure_ip_update_keyboard
from bot.states.buy import BuyStates

router = Router(name="buy")
logger = structlog.get_logger(__name__)

WEB_SERVER_BASE_URL = get_settings().public_web_base_url
TEST_ACCOUNT_DURATION_HOURS = 2


async def _safe_edit_or_reply(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """Safely edits bot messages or replies to user interactions."""
    bot = getattr(callback, "bot", None)
    bot_id = getattr(bot, "id", None)
    if callback.message and callback.message.from_user and bot_id is not None and callback.message.from_user.id == bot_id:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return
        except Exception:
            pass
    await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
    """Generates the inline keyboard for automatic and manual IP registrations."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
    builder.adjust(1)
    return builder.as_markup()


def format_duration_fa(hours: int) -> str:
    """Helper to cleanly format duration hours into Persian texts."""
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} روز"
    return f"{hours} ساعت"


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


def format_datetime_fa(value: datetime | None) -> str:
    """Formats standard datetimes into Shamsi format cleanly."""
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


async def _get_active_test_service(session: AsyncSession, user_id: int, now: datetime) -> VPNService | None:
    stmt = (
        select(VPNService)
        .where(
            VPNService.user_id == user_id,
            VPNService.is_test_account.is_(True),
            VPNService.status == VPNServiceStatus.ACTIVE.value,
            VPNService.expire_at > now,
        )
        .order_by(VPNService.expire_at.desc(), VPNService.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def _get_latest_test_service(session: AsyncSession, user_id: int) -> VPNService | None:
    stmt = (
        select(VPNService)
        .where(
            VPNService.user_id == user_id,
            VPNService.is_test_account.is_(True),
        )
        .order_by(VPNService.created_at.desc(), VPNService.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_controld_device_ips(device_id: str, settings: Settings) -> dict:
    """Queries Control D to retrieve legacy IPv4 endpoint resolvers."""
    url = f"https://api.controld.com/devices/{device_id}"
    headers = {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                body = data.get("body", {})
                resolver_info = body.get("resolvers") or body.get("resolver") or []
                v4_list = resolver_info.get("v4") or resolver_info.get("legacy", {}).get("ipv4") or []
                return {
                    "ipv4_primary": v4_list[0] if len(v4_list) > 0 else "76.76.2.22",
                    "ipv4_secondary": v4_list[1]  if len(v4_list) > 1 else "76.76.10.22"
                }
        except Exception:
            pass
    return {
        "ipv4_primary": "76.76.2.22",
        "ipv4_secondary": "76.76.10.22"
    }


# ============================================================================
# 1. MAIN DNS PLANS MENU
# ============================================================================

@router.message(F.text == texts.BTN_BUY)
@router.callback_query(F.data == "buy_back_to_plans", StateFilter("*"))
async def show_plans(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user_id = event.from_user.id if event.from_user else 0
    user = await UsersRepository(session).get_by_telegram_id(user_id) if user_id else None
    
    if user is None or not user.is_phone_verified:
        from bot.keyboards.verification import phone_verification_keyboard
        from bot.states.wallet import VerificationStates
        await state.set_state(VerificationStates.waiting_contact)
        await state.update_data(next_section="buy")
        
        prompt_text = "⚠️ برای خرید اشتراک DNS، ابتدا باید شماره موبایل خود را تایید کنید.\n\nلطفاً دکمه زیر را بزنید تا شماره تماس شما ارسال شود 👇"
        if isinstance(event, CallbackQuery):
            await event.answer()
            await event.message.answer(prompt_text, reply_markup=phone_verification_keyboard())
        else:
            await event.answer(prompt_text, reply_markup=phone_verification_keyboard())
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    plans = await PlansRepository(session).list_active()
    if not plans:
        msg = "در حال حاضر پلن فعالی برای خرید وجود ندارد."
        if isinstance(event, CallbackQuery):
            await event.message.answer(msg, reply_markup=main_menu_keyboard())
        else:
            await event.answer(msg, reply_markup=main_menu_keyboard())
        return

    builder = InlineKeyboardBuilder()
    for plan in plans:
        formatted_price = f"{plan.price:,}"
        builder.button(
            text=f"🔹 {plan.title} - {formatted_price} تومان 🔹",
            callback_data=PlanCallback(plan_id=plan.id)
        )
    builder.button(text="🎁 دریافت اکانت تست (۲ ساعته) 🆓", callback_data="get_test_account")
    builder.button(text=texts.BTN_BACK, callback_data="buy_back_to_menu")
    builder.adjust(1)

    text = (
        "لطفا یکی از پلن‌های زیر را انتخاب کنید:\n\n"
        "در صورتی که قبلا یک پلن فعال داشته باشید و پلن جدید خریداری کنید ، "
        "مدت زمان پلن جدید به پلن قبلی شما اضافه خواهد شد\n\n"
        "در صورت تمدید پلن، بخاطر انتخاب مجدد شما 10 درصد تخفیف بصورت دائمی "
        "بصورت اتوماتیک برای شما در نظر گرفته می‌شود!"
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "buy_back_to_menu")
async def buy_back_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


# ============================================================================
# 2. THE TEST ACCOUNT FLOW (RE-ROUTED TO STATIC BALANCER SLOTS)
# ============================================================================

# bot/routers/buy.py
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from html import escape
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton

from app.config import SLOT_CONFIGS
from app.models import IPAuthToken, VPNService, VPNServiceStatus
from app.repositories.users import UsersRepository


# ============================================================================
# STEP 1: RENDER THE PREFERRED SERVER LOCATION MENU
# ============================================================================

@router.callback_query(F.data == "get_test_account", StateFilter("*"))
async def handle_get_test_account(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    answer_callback: bool = True,
) -> None:
    """Prompts the user to select their preferred country/server slot for the 2-hour trial [cite: 1]."""
    if callback.message is None or callback.from_user is None:
        return

    if answer_callback:
        await callback.answer()

    await state.clear()

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.message.answer("ابتدا /start را ارسال کنید.")
        return

    # Check for active tests
    now = datetime.now(timezone.utc).replace(microsecond=0)
    active_test = await _get_active_test_service(session, user.id, now)
    if active_test is not None:
        await _safe_edit_or_reply(
            callback,
            "⚠️ شما در حال حاضر یک اکانت تست فعال دارید.\n\n"
            f"👤 نام دستگاه: <code>{escape(active_test.username.split('|')[0])}</code>\n"
            f"🗓 تاریخ انقضا: {format_datetime_fa(active_test.expire_at)}\n"
            f"⏳ زمان باقی‌مانده: {calculate_remaining_time_fa(active_test.expire_at)}",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Check for used tests
    existing_test = await _get_latest_test_service(session, user.id)
    if existing_test is not None:
        await _safe_edit_or_reply(
            callback,
            "❌ شما قبلا از اکانت تست استفاده کرده‌اید و امکان دریافت مجدد وجود ندارد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Dynamically build inline keyboard from your 5 static slots
    builder = InlineKeyboardBuilder()
    for slot_num, config in SLOT_CONFIGS.items():
        builder.button(
            text=config["name"],
            callback_data=f"test_loc:{slot_num}"  # Sends selected slot ID directly
        )
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await _safe_edit_or_reply(
        callback,
        "🎁 <b>دریافت اکانت تست ۲ ساعته رایگان</b>\n\n"
        "🗺 لطفاً کشور (سرور) مورد نظر خود را برای اکانت تست انتخاب کنید:",
        reply_markup=builder.as_markup(),
    )


# ============================================================================
# STEP 2 & 3: PROCESS SELECTION, CREATE TRIAL & RETURN WORKING DYNAMIC BUTTONS
# ============================================================================

@router.callback_query(F.data.startswith("test_loc:"), StateFilter("*"))
async def handle_test_loc_selection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Registers the 2-hour test on the chosen static server and issues secure IP registration link [cite: 1]."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    slot_num = int(parts[1])

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    # Validate active/used tests
    now = datetime.now(timezone.utc).replace(microsecond=0)
    active_test = await _get_active_test_service(session, user.id, now)
    if active_test is not None:
        await callback.message.edit_text("⚠️ شما در حال حاضر یک اکانت تست فعال دارید.", reply_markup=None)
        return

    existing_test = await _get_latest_test_service(session, user.id)
    if existing_test is not None:
        await callback.message.edit_text("❌ شما قبلا از اکانت تست استفاده کرده‌اید.", reply_markup=None)
        return

    # Disable buttons during processing to prevent rapid double-clicks
    await callback.message.edit_text("⚙️ در حال ساخت دی‌ان‌اس تست ۲ ساعته شما...", reply_markup=None)

    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات انتخاب شده معتبر نیست.")
        return

    device_id = SLOT_CONFIGS[slot_num]["device_id"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    expire_at = now + timedelta(hours=2)
    random_hex = secrets.token_hex(4)
    
    # Save custom metadata in username column: username|service_pk|slot_num
    unique_device_name = f"tg-test-{user.telegram_id}-{random_hex}|default|{slot_num}"

    # Create subscription record
    new_test_sub = VPNService(
        user_id=user.id,
        plan_id=None,
        controld_device_id=device_id,
        config_link="sdns://placeholder",
        subscription_link="sdns://placeholder",
        username=unique_device_name,
        expire_at=expire_at,
        status="active",
        is_test_account=True
    )
    session.add(new_test_sub)
    await session.flush()  # Populate Sub ID

    # Secure Phase 8 Token Generation
    # Clear stale unused tokens first
    await session.execute(
        delete(IPAuthToken).where(
            IPAuthToken.service_id == new_test_sub.id,
            IPAuthToken.is_used == False
        )
    )
    
    secure_token = uuid.uuid4().hex
    token_record = IPAuthToken(
        token=secure_token,
        service_id=new_test_sub.id,
        expires_at=now + timedelta(minutes=10),
        is_used=False
    )
    session.add(token_record)
    await session.commit()
    await state.clear()

    # Success message card matching your Jalali formatting requirement
    duration_text = "۲ ساعت"
    expire_str = format_datetime_fa(expire_at)

    success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
🔷 زمان باقی‌مانده: {duration_text}
دی ان اس اختصاصی شما :

🔷 Primary : <code>{ipv4_primary}</code>
🔷 Secondary : <code>{ipv4_secondary}</code>


مراحل ثبت آی‌پی :
1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید."""

    # Generate Phase 8 secure token keyboard dynamically
    # Local import to safely prevent circular dependency bugs on bot initialization [cite: 1]
    from bot.routers.services import create_secure_ip_update_keyboard
    markup = await create_secure_ip_update_keyboard(session, new_test_sub.id)

    await callback.message.answer(
        success_text,
        reply_markup=markup,
        parse_mode="HTML"
    )


# --- LOCATE AND REPLACE handle_apply_test_loc ---
@router.callback_query(F.data.startswith("apply_test_loc:"), StateFilter("*"))
async def handle_apply_test_loc(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Creates a 2-hour free test subscription bound directly to the chosen static slot [cite: buy.py, 1]."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    service_pk = parts[1]
    slot_num = int(parts[2])

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    await _safe_edit_or_reply(callback, "⚙️ در حال ساخت دی‌ان‌اس تست ۲ ساعته شما...")

    from app.config import SLOT_CONFIGS
    device_id = SLOT_CONFIGS[slot_num]["device_id"]
    ipv4_primary = SLOT_CONFIGS[slot_num]["dns_primary"]
    ipv4_secondary = SLOT_CONFIGS[slot_num]["dns_secondary"]

    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(hours=2)
    random_hex = secrets.token_hex(4)
    
    # Bundle the Slot Number inside the custom metadata username
    unique_device_name = f"tg-test-{user.telegram_id}-{random_hex}|{service_pk}|{slot_num}"

    new_test_sub = VPNService(
        user_id=user.id,
        plan_id=None,
        controld_device_id=device_id,
        config_link="sdns://placeholder",
        subscription_link="sdns://placeholder",
        username=unique_device_name,
        expire_at=expire_at,
        status="active",
        is_test_account=True
    )
    session.add(new_test_sub)
    await session.commit()
    await state.clear()

    duration_text = "۲ ساعت"
    expire_str = format_datetime_fa(expire_at)

    success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
🔷 زمان باقی‌مانده: {duration_text}
دی ان اس اختصاصی شما :

🔷 Primary : <code>{ipv4_primary}</code>
🔷 Secondary : <code>{ipv4_secondary}</code>


مراحل ثبت آی‌پی :
1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید.

📌 برای تغییر لوکیشن بازی به لوکیشن کشور دلخواه خود: به بخش «اشتراک‌های من» بروید، روی «مدیریت» کلیک کنید و لوکیشن دلخواه را تنظیم کنید."""

    await callback.message.answer(
        success_text, 
        markup = await create_secure_ip_update_keyboard(session, new_test_sub.id), # For test
        parse_mode="HTML"
    )


# ============================================================================
# 3. CHOOSE PLAN, CATEGORY, GAME & LOCATION SELECTION FLOW
# ============================================================================

# --- LOCATE AND REPLACE handle_buy_plan_select ---
@router.callback_query(PlanCallback.filter(), StateFilter("*"))
async def handle_buy_plan_select(
    callback: CallbackQuery,
    callback_data: PlanCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    plan_id = callback_data.plan_id
    stmt = select(Plan).where(Plan.id == plan_id)
    result = await session.execute(stmt)
    plan = result.scalars().first()

    if plan is None or not plan.is_active:
        await callback.message.answer("❌ این طرح دیگر فعال نیست.")
        return

    # Strictly display only Default Traffic and Gaming Categories
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 کل ترافیک اینترنت (Default)", callback_data=f"buy_plan_srv:{plan.id}:default")
    builder.button(text="🎮 بازی‌ها (Gaming)", callback_data=f"srv_cat:{plan.id}:gaming:0")
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await _safe_edit_or_reply(
        callback,
        f"⚡ پلن انتخاب شده: <b>{escape(plan.title)}</b>\n\n"
        f"🗺 ابتدا دسته‌بندی ترافیکی مورد نظر خود را انتخاب کنید:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("srv_cat:"), StateFilter("*"))
async def handle_srv_cat(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    plan_id = int(parts[1])
    category_key = parts[2]
    page = int(parts[3])
    
    profile_id = settings.controld_profile_id or "default"
    controld = ControlDService(settings)
    services = await controld.fetch_controld_services(profile_id)
    if not services:
        await _safe_edit_or_reply(callback, "❌ خطایی در بارگذاری سرویس‌ها رخ داد.")
        return
        
    filtered = [s for s in services if s["category"] == category_key]
    filtered.sort(key=lambda x: (x["name"] or "").lower())
    
    limit = 10
    start_idx = page * limit
    end_idx = start_idx + limit
    page_items = filtered[start_idx:end_idx]
    has_next = len(filtered) > end_idx

    builder = InlineKeyboardBuilder()
    for s in page_items:
        builder.button(
            text=s["name"] or s["pk"],
            callback_data=f"buy_plan_srv:{plan_id}:{s['pk']}"
        )
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"srv_cat:{plan_id}:{category_key}:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"srv_cat:{plan_id}:{category_key}:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
        
    builder.row(InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data=PlanCallback(plan_id=plan_id).pack()))
    builder.adjust(2)
    
    from app.services.controld import get_category_label_fa
    category_label = get_category_label_fa(category_key)
    await _safe_edit_or_reply(
        callback,
        f"📂 دسته‌بندی انتخاب شده: <b>{category_label}</b> | صفحه {page + 1}\n\n"
        f"🎮 لطفاً سرویس مورد نظر خود را برای انتقال ترافیک انتخاب کنید:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("buy_plan_srv:"), StateFilter("*"))
async def handle_buy_plan_srv(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]

    controld_service = ControlDService(settings)
    proxies = await controld_service.fetch_controld_proxies()
    
    if not proxies:
        await callback.message.answer("❌ خطایی در بارگذاری سرورهای معتبر رخ داد.")
        return

    await _show_buy_loc_page(callback, plan_id, service_pk, page=0, settings=settings)



# --- LOCATE AND REPLACE _show_buy_loc_page ---
async def _show_buy_loc_page(callback: CallbackQuery, plan_id: int, service_pk: str, page: int, settings: Settings) -> None:
    """Renders the selection menu showing exactly your 5 premium static servers [cite: 1]."""
    from app.config import SLOT_CONFIGS

    builder = InlineKeyboardBuilder()
    for slot_num, config in SLOT_CONFIGS.items():
        builder.button(
            text=config["name"],
            callback_data=f"buy_plan_loc:{plan_id}:{service_pk}:{slot_num}" # We pass the Slot Number!
        )

    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=PlanCallback(plan_id=plan_id).pack()))

    await _safe_edit_or_reply(
        callback,
        "🗺 <b>انتخاب لوکیشن سرور</b>\n\n"
        "لطفاً کشور (سرور) مورد نظر خود را برای انتقال ترافیک انتخاب کنید:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("buy_loc_page:"), StateFilter("*"))
async def handle_buy_loc_page(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]
    page = int(parts[3])
    await _show_buy_loc_page(callback, plan_id, service_pk, page, settings)


@router.callback_query(F.data.startswith("buy_plan_loc:"), StateFilter("*"))
async def handle_buy_plan_loc(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]
    pop_code = parts[3]

    stmt = select(Plan).where(Plan.id == plan_id)
    result = await session.execute(stmt)
    plan = result.scalars().first()

    if plan is None:
        await callback.message.answer("❌ طرح پیدا نشد.")
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    # Resolve Game/Service display name
    service_display = service_pk.capitalize()
    if service_pk == "default":
        service_display = "🌐 کل ترافیک اینترنت"
    else:
        controld_service = ControlDService(settings)
        services = await controld_service.fetch_controld_services(settings.controld_profile_id or "default")
        if services:
            for s in services:
                if s["pk"] == service_pk:
                    service_display = s["name"] or service_pk.capitalize()
                    break

    # Resolve Country display name
    controld_service = ControlDService(settings)
    proxies = await controld_service.fetch_controld_proxies()
    country_display = pop_code
    if proxies:
        for p in proxies:
            if p["code"] == pop_code:
                country_display = f"{p['country_name']} - {p['city_name']} ({p['code']})"
                break

    duration_hours = plan.duration_hours or 720
    duration_text = format_duration_fa(duration_hours)

    active_stmt = select(VPNService).where(
        VPNService.user_id == user.id,
        VPNService.status == "active"
    )
    active_result = await session.execute(active_stmt)
    current_sub = active_result.scalars().first()

    final_price = plan.price
    discount_msg = ""
    if current_sub is not None:
        discount_amount = int(plan.price * 0.1)
        final_price = plan.price - discount_amount
        discount_msg = f"🎁 تخفیف تمدید فعال: {discount_amount:,} تومان\n"

    invoice_text = f"""🧾 پیش‌فاکتور خرید اشتراک DNS

⚡ نام سرویس: {escape(plan.title)}
🗓 مدت اعتبار: {duration_text}
🎮 برنامه/بازی: <b>{escape(service_display)}</b>
🗺 سرور (کشور): <b>{escape(country_display)}</b>
💵 قیمت طرح: {plan.price:,} تومان
{discount_msg}💵 قیمت نهایی شما: {final_price:,} تومان
🏦 موجودی فعلی شما: {user.wallet_balance:,} تومان

آیا مایل هستید این طرح را خریداری کنید؟"""

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 پرداخت آنلاین", callback_data=f"pay_online_paystar:{plan.id}:{service_pk}:{pop_code}")
    builder.button(text="🏦 پرداخت از کیف پول (آنی)", callback_data=f"pay_instant_wallet:{plan.id}:{service_pk}:{pop_code}")
    builder.button(text="💳 کارت به کارت (دستی)", callback_data=f"pay_manual_card:{plan.id}:{service_pk}:{pop_code}")
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await _safe_edit_or_reply(callback, invoice_text, reply_markup=builder.as_markup())


# ============================================================================
# 4. INSTANT PAYMENT FROM WALLET (RE-ROUTED TO STATIC BALANCER SLOTS)
# ============================================================================

@router.callback_query(F.data.startswith("pay_instant_wallet:"), StateFilter("*"))
async def handle_pay_instant_wallet(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Processes wallet checkouts by allocating slot from permanent load balancer [cite: buy.py, 1]."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]
    pop_code = parts[3]

    stmt = select(Plan).where(Plan.id == plan_id)
    result = await session.execute(stmt)
    plan = result.scalars().first()

    if plan is None:
        await callback.message.answer("❌ طرح مورد نظر پیدا نشد.")
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    active_stmt = select(VPNService).where(
        VPNService.user_id == user.id,
        VPNService.status == "active"
    )
    active_result = await session.execute(active_stmt)
    current_sub = active_result.scalars().first()

    final_price = plan.price
    if current_sub is not None:
        final_price = plan.price - int(plan.price * 0.1)

    if user.wallet_balance < final_price:
        await callback.message.answer(
            f"❌ موجودی کیف پول کافی نیست.\n"
            f"قیمت نهایی طرح: {final_price:,} تومان\n"
            f"موجودی شما: {user.wallet_balance:,} تومان\n\n"
            f"لطفاً گزینه 'کارت به کارت دستی' را برای شارژ حساب خود انتخاب کنید."
        )
        return

    await callback.message.answer("⚙️ در حال پردازش تراکنش و فعال‌سازی اشتراک دی‌ان‌اس...")

    now = datetime.now(timezone.utc)
    profile_id = plan.controld_profile_id or settings.controld_profile_id

    if current_sub is None:
        # Allocate slot from permanent balancer pool [cite: 1]
        try:
            device_id = await get_least_populated_personal_slot(session)
        except Exception as exc:
            await callback.message.answer(f"❌ خطا در تخصیص اسلات دی‌ان‌اس: {str(exc)}")
            return

        expire_at = now + timedelta(hours=plan.duration_hours)
        random_hex = secrets.token_hex(4)
        
        # Save persistent metadata string to DB for correct dashboard rendering [cite: services.py]
        unique_device_name = f"tg-user-{user.telegram_id}-{random_hex}|{service_pk}|{pop_code}"

        # Fetch DNS resolvers [cite: 1]
        device_data = await get_controld_device_ips(device_id, settings)
        ipv4_primary = device_data["ipv4_primary"]
        ipv4_secondary = device_data["ipv4_secondary"]

        # Apply chosen routing country directly [cite: buy.py]
        # controld_service = ControlDService(settings)
        # if service_pk == "default":
        #     await controld_service.update_profile_default(profile_id, pop_code)  
        # else:
        #     await controld_service.update_service_route(profile_id, service_pk, pop_code)  

        new_subscription = VPNService(
            user_id=user.id,
            plan_id=plan.id,
            controld_device_id=device_id,
            config_link="sdns://placeholder",
            subscription_link="sdns://placeholder",
            username=unique_device_name,
            expire_at=expire_at,
            status="active"
        )
        session.add(new_subscription)
        
    else:
        # Renewal - Simply extend time, avoid setting a custom TTL on Control D [cite: buy.py, 1]
        current_expire = current_sub.expire_at
        if current_expire.tzinfo is None:
            current_expire = current_expire.replace(timezone.utc)

        expire_at = current_expire + timedelta(hours=plan.duration_hours)
        current_sub.expire_at = expire_at
        current_sub.plan_id = plan.id

        device_id = current_sub.controld_device_id
        
        # Apply chosen routing country directly [cite: buy.py]
        # controld_service = ControlDService(settings)
        # if service_pk == "default":
        #     await controld_service.update_profile_default(profile_id, pop_code)  
        # else:
        #     await controld_service.update_service_route(profile_id, service_pk, pop_code)  

        # Fetch DNS resolvers [cite: 1]
        device_data = await get_controld_device_ips(device_id, settings)
        ipv4_primary = device_data["ipv4_primary"]
        ipv4_secondary = device_data["ipv4_secondary"]

    # Balance deduction  
    user.wallet_balance -= final_price
    await session.commit()
    await state.clear()

    # Format Expiration
    duration_text = calculate_remaining_time_fa(expire_at)  
    expire_str = format_datetime_fa(expire_at)

    success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
🔷 زمان باقی‌مانده: {duration_text}
دی ان اس اختصاصی شما :

🔷 Primary : <code>{ipv4_primary}</code>
🔷 Secondary : <code>{ipv4_secondary}</code>


مراحل ثبت آی‌پی :
1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید.

📌 برای تغییر لوکیشن بازی به لوکیشن کشور دلخواه خود: به بخش «اشتراک‌های من» بروید، روی «مدیریت» کلیک کنید و لوکیشن دلخواه را تنظیم کنید."""

    await callback.message.answer(
        success_text, 
        markup = await create_secure_ip_update_keyboard(session, new_subscription.id), # For wallet
        parse_mode="HTML"
    )


# ============================================================================
# 5. CARD-TO-CARD MANUAL BILLING FLOW WITH LOCATION PASSING  
# ============================================================================

@router.callback_query(F.data.startswith("pay_manual_card:"), StateFilter("*"))
async def handle_pay_manual_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]
    pop_code = parts[3]

    plan = await PlansRepository(session).get(plan_id)
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)

    if plan is None or user is None:
        await callback.message.answer("خطا در پردازش درخواست.")
        return

    active_stmt = select(VPNService).where(
        VPNService.user_id == user.id,
        VPNService.status == "active"
    )
    active_result = await session.execute(active_stmt)
    current_sub = active_result.scalars().first()

    final_price = plan.price
    if current_sub is not None:
        final_price = plan.price - int(plan.price * 0.1)

    # Append chosen location (POP code) and game_pk directly into order custom_username
    custom_username = f"dns_user_{user.telegram_id}|{service_pk}|{pop_code}"

    # Build database order & payment records
    order_service = OrderService(session, settings)
    order, payment = await order_service.create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=custom_username,
        discount_code=None,
        discount_percent=10 if current_sub is not None else 0,
        discount_amount=int(plan.price * 0.1) if current_sub is not None else 0,
    )

    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(order_id=order.id, payment_id=payment.id)

    app_settings = AppSettingsService(session)
    card_number = await app_settings.get_payment_card_number()
    card_holder = await app_settings.get_payment_card_holder()
    payment_description = await app_settings.get_payment_description()
    description_text = f"\nتوضیحات پرداخت:\n{escape(payment_description)}\n" if payment_description else ""

    await callback.message.answer(
        f"""💳 پرداخت دستی (کارت به کارت)

مبلغ قابل پرداخت:
{format_money(final_price)} تومان

شماره کارت:
`{escape(card_number) or 'ثبت نشده'}`

به نام:
{escape(card_holder) or 'ثبت نشده'}
{description_text}

بعد از پرداخت، تصویر رسید را همینجا ارسال کنید تا ادمین‌ها حساب شما را شارژ و اشتراک را فعال کنند."""
    )


@router.callback_query(F.data.startswith("pay_online_paystar:"), StateFilter("*"))
async def handle_pay_online_paystar(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_pk = parts[2]
    pop_code = parts[3]

    plan = await PlansRepository(session).get(plan_id)
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if plan is None or user is None:
        await callback.message.answer("خطا در پردازش درخواست.")
        return

    profile_id = plan.controld_profile_id or settings.controld_profile_id
    if not profile_id:
        await callback.message.answer("❌ تنظیمات Control D برای این طرح کامل نیست.")
        return

    active_stmt = select(VPNService).where(
        VPNService.user_id == user.id,
        VPNService.status == "active",
    )
    active_result = await session.execute(active_stmt)
    current_sub = active_result.scalars().first()

    final_price = plan.price
    if current_sub is not None:
        final_price = plan.price - int(plan.price * 0.1)

    custom_username = f"dns_user_{user.telegram_id}|{service_pk}|{pop_code}"
    order_service = OrderService(session, settings)
    order, payment = await order_service.create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=custom_username,
        discount_code=None,
        discount_percent=10 if current_sub is not None else 0,
        discount_amount=int(plan.price * 0.1) if current_sub is not None else 0,
        payment_method="paystar",
        commit=False,
    )

    callback_url = f"{settings.public_web_base_url}/paystar/callback"
    paystar = PaystarService()
    token = await paystar.create_payment(
        amount_toman=payment.amount,
        order_id=order.tracking_code,
        callback_url=callback_url,
    )

    if not token:
        await session.rollback()
        await callback.message.answer(
            "❌ ساخت درگاه بانکی ناموفق بود. لطفاً دوباره تلاش کنید یا روش دیگری را انتخاب کنید.",
            reply_markup=main_menu_keyboard(),
        )
        return

    payment.token = token
    payment.method = "paystar"
    payment.status = PaymentStatus.PENDING.value
    await session.commit()
    await state.clear()

    redirect_url = f"{settings.public_web_base_url}/paystar/redirect?token={token}"
    await callback.message.answer(
        f"""💳 درگاه پرداخت بانکی آماده است

مبلغ قابل پرداخت:
{format_money(final_price)} تومان

کد پیگیری سفارش:
<code>{order.tracking_code}</code>

⚠️ لطفاً پیش از ورود به درگاه پرداخت، وی‌پی‌ان (VPN) خود را خاموش کنید.

برای تکمیل پرداخت روی دکمه زیر بزنید.""",
        reply_markup=paystar_payment_keyboard(redirect_url),
    )


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    payment_id = data.get("payment_id")
    order = await OrdersRepository(session).get_with_details(int(order_id)) if order_id else None
    payment = await PaymentsRepository(session).get(int(payment_id)) if payment_id else None

    if order is None or payment is None:
        await state.clear()
        await message.answer("پرداخت پیدا نشد. لطفاً دوباره سفارش ثبت کنید.", reply_markup=main_menu_keyboard())
        return

    order_service = OrderService(session, settings)
    if await order_service.expire_order_if_unpaid(order):
        await state.clear()
        await message.answer(texts.EXPIRED_ORDER_TEXT, reply_markup=main_menu_keyboard())
        return

    receipt_file_id = message.photo[-1].file_id
    await PaymentService(session, VPNPanelService(), settings).attach_receipt(payment, receipt_file_id)
    await state.clear()

    await message.answer("✅ رسید شما دریافت شد و در انتظار تایید ادمین است.")

    from bot.notifications import notify_admins_order_payment 

    sent_count = await notify_admins_order_payment(
        bot=message.bot,
        session=session,
        settings=settings,
        payment=payment,
        order=order,
        receipt_file_id=receipt_file_id,
    )
    if sent_count == 0:
        await message.answer("رسید دریافت شد، اما ادمینی برای بررسی تنظیم نشده است. لطفاً با پشتیبانی تماس بگیرید.")


# ============================================================================
# MANUAL IP REGISTRATION FSM FLOW
# ============================================================================

@router.callback_query(F.data.startswith("manual_ip_reg:"), StateFilter("*"))
async def handle_manual_ip_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    device_id = callback.data.split(":")[1]
    
    await state.set_state(BuyStates.waiting_manual_ip)
    await state.update_data(device_id=device_id)
    
    await callback.message.answer(
        "🤖 لطفاً آی‌پی (IPv4) خود را بدون فیلترشکن وارد کنید.\n\n"
        "مثال: `5.200.12.1`"
    )


@router.message(BuyStates.waiting_manual_ip, F.text)
async def process_manual_ip(
    message: Message, 
    state: FSMContext, 
    session: AsyncSession, 
    settings: Settings
) -> None:
    user_ip = message.text.strip()
    
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", user_ip):
        await message.answer("❌ فرمت آی‌پی نامعتبر است. لطفاً یک آی‌پی عددی معتبر ارسال کنید.")
        return

    data = await state.get_data()
    device_id = data.get("device_id")
    if not device_id:
        await state.clear()
        await message.answer("❌ خطای سیستمی. لطفاً مجدداً تلاش کنید.")
        return

    stmt = select(VPNService).where(VPNService.controld_device_id == device_id, VPNService.status == "active").limit(1)
    res = await session.execute(stmt)
    service = res.scalars().first()

    if not service:
        await state.clear()
        await message.answer("❌ دستگاه فعال متناظر با این اشتراک یافت نشد.")
        return

    from app.services.ip_manager import update_device_ip_safe
    success = await update_device_ip_safe(session, service, user_ip)

    if success:
        await state.clear()
        await message.answer(f"✅ آی‌پی <code>{user_ip}</code> با موفقیت به صورت دستی برای دستگاه شما ثبت شد.", parse_mode="HTML")
    else:
        await message.answer(f"❌ خطا در ثبت آی‌پی در سیستم Control D. مجدداً تلاش کنید.")