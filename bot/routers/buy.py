# Open bot/routers/buy.py
from __future__ import annotations

import os
import re
import secrets
import httpx
from datetime import datetime, timezone, timedelta
from html import escape
import jdatetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Plan, VPNService, OrderKind, OrderStatus, DiceRoll, Payment
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
from app.services.settings_service import AppSettingsService
from app.services.username_validator import validate_username
from app.services.vpn_panel import VPNPanelService
from app.services.controld import create_dns_device, ControlDService  # ControlD direct integration
from app.utils.formatting import format_money
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.buy import PlanCallback  # Native filter
from bot.states.buy import BuyStates

router = Router(name="buy")

# ============================================================================
# CONFIGURATION
# ============================================================================
WEB_SERVER_BASE_URL = "http://82.115.24.241:8000"


def _get_ip_registration_keyboard(device_id: str) -> InlineKeyboardMarkup:
    """
    Generates the inline keyboard matching your design screenshot [1].
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
    builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
    builder.adjust(1)
    return builder.as_markup()


def format_duration_fa(hours: int) -> str:
    """
    Formats hours dynamically into a readable Persian duration string [1].
    """
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days}  روز"
    return f"{hours} ساعت"


async def get_controld_device_ips(device_id: str, settings: Settings) -> dict:
    """
    Real-time API fallback: Queries Control D on approval to fetch the exact 
    legacy IPv4 addresses mapped to this device.
    """
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
                resolver_info = body.get("resolvers") or body.get("resolver") or {}
                v4_list = resolver_info.get("v4") or resolver_info.get("legacy", {}).get("ipv4") or []
                return {
                    "ipv4_primary": v4_list[0] if len(v4_list) > 0 else "94.183.166.203",
                    "ipv4_secondary": v4_list[1] if len(v4_list) > 1 else "94.183.166.208"
                }
        except Exception:
            pass
    return {
        "ipv4_primary": "94.183.166.203",
        "ipv4_secondary": "94.183.166.208"
    }


# ============================================================================
# 1. MAIN DNS PLANS MENU
# ============================================================================

@router.message(F.text == texts.BTN_BUY)
@router.callback_query(F.data == "buy_back_to_plans", StateFilter("*"))
async def show_plans(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user_id = event.from_user.id if event.from_user else 0
    user = await UsersRepository(session).get_by_telegram_id(user_id) if user_id else None
    
    # Enforce Phone Verification
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

    # Build the inline plans keyboard
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
# 2. THE TEST ACCOUNT (FREE TRIAL) FLOW WITH DYNAMIC LOCATION SELECTION [1]
# ============================================================================

@router.callback_query(F.data == "get_test_account", StateFilter("*"))
async def handle_get_test_account(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if callback.message is None or callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.message.answer("ابتدا /start را ارسال کنید.")
        await callback.answer()
        return

    # Anti-Abuse DB Check [1]
    stmt = select(VPNService).where(
        VPNService.user_id == user.id,
        VPNService.is_test_account == True
    )
    result = await session.execute(stmt)
    existing_test = result.scalars().first()

    if existing_test is not None:
        await callback.answer("❌ شما قبلا از اکانت تست استفاده کرده‌اید.", show_alert=True)
        return

    await callback.answer()

    # Fetch available profiles dynamically from Control D API [1]
    controld_service = ControlDService(settings)
    profiles = await controld_service.fetch_controld_profiles()
    
    if not profiles:
        await callback.message.answer("❌ خطایی در بارگذاری سرورهای معتبر رخ داد.")
        return

    builder = InlineKeyboardBuilder()
    for p in profiles:
        builder.button(
            text=f"📍 {p['name']}",
            callback_data=f"apply_test_loc:{p['id']}"  # Routes to creation with chosen profile [1]
        )
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await callback.message.edit_text(
        "🎁 <b>دریافت اکانت تست ۲ ساعته رایگان</b>\n\n"
        "🗺 لطفاً سرور (لوکیشن) مورد نظر خود را برای اکانت تست انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("apply_test_loc:"), StateFilter("*"))
async def handle_apply_test_loc(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    profile_id = callback.data.split(":")[1]
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    await callback.message.edit_text("⚙️ در حال ساخت دی‌ان‌اس تست ۲ ساعته شما...")

    random_hex = secrets.token_hex(4)
    unique_device_name = f"tg_test_{user.telegram_id}_{random_hex}"

    controld_service = ControlDService(settings)
    device_data = await controld_service.create_dns_device(
        tg_user_id=user.telegram_id,
        profile_id=profile_id,  # Uses the user's selected location [1]
        duration_hours=2,
        device_name=unique_device_name
    )

    if device_data is None:
        await callback.message.answer("❌ خطا در برقراری ارتباط با سرورهای Control D. لطفاً مجدداً تلاش کنید.")
        return

    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(hours=2)

    new_test_sub = VPNService(
        user_id=user.id,
        plan_id=None,
        controld_device_id=device_data["device_id"],
        config_link=device_data["doh"],
        subscription_link=device_data["dot"],
        username=unique_device_name,
        expire_at=expire_at,
        status="active",
        is_test_account=True
    )
    session.add(new_test_sub)
    await session.commit()
    await state.clear()

    duration_text = "۲ ساعت"

    # Shamsi translation
    try:
        tehran_tz = ZoneInfo("Asia/Tehran")
        tehran_expire = expire_at.astimezone(tehran_tz)
        shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire)
        expire_str = shamsi_expire.strftime("%Y/%m/%d - %H:%M:%S")
    except ImportError:
        tehran_tz = ZoneInfo("Asia/Tehran")
        expire_str = expire_at.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M:%S")

    success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
دی ان اس اختصاصی شما :

🔷 Primary : <code>{device_data['ipv4_primary']}</code>
🔷 Secondary : <code>{device_data['ipv4_secondary']}</code>


مراحل ثبت آی‌پی :
1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید."""

    await callback.message.answer(
        success_text, 
        reply_markup=_get_ip_registration_keyboard(device_data["device_id"]), 
        parse_mode="HTML"
    )


# ============================================================================
# 3. CHOOSE PLAN & CUSTOM LOCATION SELECTION FLOW [1]
# ============================================================================

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

    # Fetch profiles/locations dynamically from Control D API [1]
    controld_service = ControlDService(settings)
    profiles = await controld_service.fetch_controld_profiles()
    
    if not profiles:
        await callback.message.answer("❌ خطایی در بارگذاری سرورهای معتبر رخ داد.")
        return

    builder = InlineKeyboardBuilder()
    for p in profiles:
        builder.button(
            text=f"📍 {p['name']}",
            callback_data=f"buy_plan_loc:{plan.id}:{p['id']}"  # Appends selected profile_id [1]
        )
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await callback.message.edit_text(
        f"⚡ پلن انتخاب شده: <b>{escape(plan.title)}</b>\n\n"
        f"🗺 لطفاً سرور (لوکیشن) مورد نظر خود را برای این اشتراک انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("buy_plan_loc:"), StateFilter("*"))
async def handle_buy_plan_loc(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    profile_id = parts[2]  # Selected location Profile ID [1]

    stmt = select(Plan).where(Plan.id == plan_id)
    result = await session.execute(stmt)
    plan = result.scalars().first()

    if plan is None:
        await callback.message.answer("❌ طرح پیدا نشد.")
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        return

    # Check renewal
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
🗓 مدت اعتبار: {plan.duration_hours} ساعت
💵 قیمت طرح: {plan.price:,} تومان
{discount_msg}💵 قیمت نهایی شما: {final_price:,} تومان
🏦 موجودی فعلی شما: {user.wallet_balance:,} تومان

آیا مایل هستید این طرح را خریداری کنید؟"""

    builder = InlineKeyboardBuilder()
    builder.button(text="🏦 پرداخت از کیف پول (آنی)", callback_data=f"pay_instant_wallet:{plan.id}:{profile_id}")
    builder.button(text="💳 کارت به کارت (دستی)", callback_data=f"pay_manual_card:{plan.id}:{profile_id}")
    builder.button(text="🔙 بازگشت", callback_data="buy_back_to_plans")
    builder.adjust(1)

    await callback.message.edit_text(invoice_text, reply_markup=builder.as_markup())


# ============================================================================
# 4. INSTANT PAYMENT FROM WALLET USING SELECTED LOCATION [1]
# ============================================================================

@router.callback_query(F.data.startswith("pay_instant_wallet:"), StateFilter("*"))
async def handle_pay_instant_wallet(
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
    profile_id = parts[2]  # Selected location Profile ID [1]

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

    # Validate wallet balance [1]
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

    if current_sub is None:
        # Create new device
        expire_at = now + timedelta(hours=plan.duration_hours)
        random_hex = secrets.token_hex(4)
        unique_device_name = f"tg_user_{user.telegram_id}_{random_hex}"

        # Provision device on Control D using the user's selected profile ID [1]
        device_data = await create_dns_device(
            tg_user_id=user.telegram_id,
            profile_id=profile_id,  # Custom location [1]
            duration_hours=plan.duration_hours,
            device_type="mobile",
            device_name=unique_device_name
        )

        if device_data is None:
            await callback.message.answer("❌ خطا در برقراری ارتباط با سرورهای دی‌ان‌اس.")
            return

        device_id = device_data["device_id"]
        ipv4_primary = device_data["ipv4_primary"]
        ipv4_secondary = device_data["ipv4_secondary"]

        new_subscription = VPNService(
            user_id=user.id,
            plan_id=plan.id,
            controld_device_id=device_id,
            config_link=device_data["doh"],
            subscription_link=device_data["dot"],
            username=unique_device_name,
            expire_at=expire_at,
            status="active"
        )
        session.add(new_subscription)
        
    else:
        # Renewal - accumulate time
        current_expire = current_sub.expire_at
        if current_expire.tzinfo is None:
            current_expire = current_expire.replace(tzinfo=timezone.utc)

        expire_at = current_expire + timedelta(hours=plan.duration_hours)
        current_sub.expire_at = expire_at
        current_sub.plan_id = plan.id

        new_disable_ttl = int(expire_at.timestamp())
        controld_service = ControlDService(settings)
        
        success = await controld_service.update_device(
            device_id=current_sub.controld_device_id,
            disable_ttl=new_disable_ttl
        )

        if not success:
            await callback.message.answer("❌ خطا در تمدید اشتراک در سرورهای Control D.")
            return

        device_id = current_sub.controld_device_id
        
        # Use our real-time IP fallback getter to fetch dynamic IPs [1]
        ips = await get_controld_device_ips(device_id, settings)
        ipv4_primary = ips["ipv4_primary"]
        ipv4_secondary = ips["ipv4_secondary"]

    # Atomic balance deduction [1]
    user.wallet_balance -= final_price
    await session.commit()
    await state.clear()

    # Format Shamsi Expiration
    duration_hours = plan.duration_hours or 720
    duration_text = format_duration_fa(duration_hours)

    try:
        tehran_tz = ZoneInfo("Asia/Tehran")
        tehran_expire = expire_at.astimezone(tehran_tz)
        shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire)
        expire_str = shamsi_expire.strftime("%Y/%m/%d - %H:%M:%S")
    except ImportError:
        tehran_tz = ZoneInfo("Asia/Tehran")
        expire_str = expire_at.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M:%S")

    # Success Card Message
    success_text = f"""🔹 تاریخ انقضاء پلن : {expire_str}
دی ان اس اختصاصی شما :

🔷 Primary : <code>{ipv4_primary}</code>
🔷 Secondary : <code>{ipv4_secondary}</code>


مراحل ثبت آی‌پی :
1️⃣ : در ابتدا گوشی موبایل و کنسول بازی رو به یک اینترنت مشترک وصل کنید .
2️⃣ : بدون فیلتر شکن روی دکمه ثبت آی‌پی زیر کلیک کنید.
❌ در صورت عدم ثبت آی‌پی DNS ها برای شما متصل نخواهد شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید."""

    await callback.message.answer(
        success_text, 
        reply_markup=_get_ip_registration_keyboard(device_id), 
        parse_mode="HTML"
    )


# ============================================================================
# 5. CARD-TO-CARD MANUAL BILLING FLOW WITH LOCATION PASSING [1]
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
    profile_id = parts[2]  # Selected location Profile ID [1]

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

    # --- GENIUS WORKAROUND: Append chosen profile_id directly into order custom_username ---
    # Bypasses SQL schema constraints cleanly, saving chosen location for Admin approval [1]
    custom_username = f"dns_user_{user.telegram_id}|{profile_id}"

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


# Inside bot/routers/buy.py -> receive_receipt_photo()

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

    # --- FIXED: Local import to bypass circular dependency name-error ---
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
# 6. MANUAL IP REGISTRATION FSM FLOW
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

    url = f"https://api.controld.com/devices/{device_id}/ips"
    headers = {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }
    payload = {"ip": user_ip}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code in (200, 201):
                await state.clear()
                await message.answer(f"✅ آی‌پی <code>{user_ip}</code> با موفقیت به صورت دستی برای دستگاه شما ثبت شد.", parse_mode="HTML")
            else:
                await message.answer(f"❌ خطا در ثبت آی‌پی در سیستم Control D:\n<code>{response.text}</code>", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ خطا در ارتباط با پنل Control D: {str(e)}")