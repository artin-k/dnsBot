# bot/routers/buy.py
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings, SLOT_CONFIGS
from app.models import Plan, VPNService, PaymentStatus
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService
from app.services.paystar import PaystarService
from app.services.settings_service import AppSettingsService
from app.services.vpn_panel import VPNPanelService
from app.services.controld import ControlDService
from app.services.ip_manager import update_device_ip_safe
from app.utils.formatting import format_money, format_duration_fa, format_datetime_fa, calculate_remaining_time_fa
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.buy import PlanCallback, paystar_payment_keyboard
from bot.keyboards.verification import phone_verification_keyboard
from bot.routers.services import create_secure_ip_update_keyboard
from bot.states.buy import BuyStates
from bot.states.wallet import VerificationStates
from bot.utils.auto_clean import schedule_message_deletion
from bot.utils.ui import safe_edit_or_reply
from bot.routers.test_account import handle_get_test_account

router = Router(name="buy")
settings = get_settings()


# ============================================================================
# STEP 1: LOCATION SELECTION FIRST
# ============================================================================

@router.message(F.text == texts.BTN_BUY)
@router.callback_query(F.data.in_({"menu:buy", "buy_back_to_locations", "buy_back_to_plans"}), StateFilter("*"))
async def show_locations(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Step 1: User chooses server country first (Germany vs Turkey)."""
    user_id = event.from_user.id if event.from_user else 0
    user = await UsersRepository(session).get_by_telegram_id(user_id) if user_id else None

    # Enforce phone verification
    if not user or not user.is_phone_verified:
        await state.set_state(VerificationStates.waiting_contact)
        await state.update_data(next_section="buy")
        prompt = "⚠️ برای خرید اشتراک DNS، ابتدا باید شماره موبایل خود را تایید کنید.\n\nدکمه زیر را لمس کنید 👇"
        await safe_edit_or_reply(event, prompt, reply_markup=phone_verification_keyboard())
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🇩🇪 آلمان (فرانکفورت)", callback_data="buy_pick_loc:1")
    builder.button(text="🇹🇷 ترکیه (استانبول)", callback_data="buy_pick_loc:5")
    builder.button(text="🎁 دریافت اکانت تست (۲ ساعته) 🆓", callback_data="get_test_account")
    builder.button(text="↩️ بازگشت به منوی اصلی", callback_data="buy_back_to_menu")
    builder.adjust(1)

    text = """🗺 <b>انتخاب لوکیشن سرور (کشور)</b>

لطفاً سرور مورد نظر خود را برای خرید اشتراک انتخاب کنید:

💡 <i>نکته: شما می‌توانید بعد از خرید هر زمان که مایل بودید لوکیشن را بین آلمان و ترکیه در بخش «اشتراک‌های من» به صورت نامحدود و رایگان تغییر دهید.</i>"""

    await safe_edit_or_reply(event, text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "buy_back_to_menu")
async def buy_back_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


# ============================================================================
# STEP 2: PLAN SELECTION SECOND
# ============================================================================

@router.callback_query(F.data.startswith("buy_pick_loc:"), StateFilter("*"))
async def handle_location_picked(callback: CallbackQuery, session: AsyncSession) -> None:
    """Step 2: Show active purchase plans for the selected location."""
    await callback.answer()
    slot_num = int(callback.data.split(":")[1])
    if slot_num not in SLOT_CONFIGS:
        slot_num = 1

    plans = await PlansRepository(session).list_active()
    if not plans:
        await callback.message.answer("در حال حاضر پلن فعالی برای خرید وجود ندارد.")
        return

    server_name = SLOT_CONFIGS[slot_num]["name"]

    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"🔹 {plan.title} - {plan.price:,} تومان 🔹",
            callback_data=f"buy_pick_plan:{slot_num}:{plan.id}",
        )
    builder.button(text="🔙 تغییر لوکیشن سرور", callback_data="buy_back_to_locations")
    builder.adjust(1)

    text = f"""🗺 <b>سرور انتخابی:</b> <b>{escape(server_name)}</b>

⚡ لطفاً پلن مورد نظر خود را انتخاب فرمایید:

💡 <i>در صورتی که پلن فعال داشته باشید، مدت زمان پلن جدید به اشتراک قبلی شما اضافه خواهد شد.</i>"""

    await safe_edit_or_reply(callback, text, reply_markup=builder.as_markup())


# ============================================================================
# STEP 3: PRE-INVOICE & CHECKOUT OPTIONS
# ============================================================================

@router.callback_query(F.data.startswith("buy_pick_plan:"), StateFilter("*"))
async def handle_plan_picked(callback: CallbackQuery, session: AsyncSession) -> None:
    """Step 3: Generate pre-invoice with payment methods for (slot_num, plan_id)."""
    await callback.answer()
    parts = callback.data.split(":")
    slot_num = int(parts[1])
    plan_id = int(parts[2])

    plan = await PlansRepository(session).get(plan_id)
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)

    if not plan or not user or slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اطلاعات نامعتبر است.")
        return

    server_name = SLOT_CONFIGS[slot_num]["name"]
    duration_text = format_duration_fa(plan.duration_hours or 720)

    invoice_text = f"""🧾 <b>پیش‌فاکتور خرید اشتراک DNS</b>

⚡ <b>نام سرویس:</b> {escape(plan.title)}
🗓 <b>مدت اعتبار:</b> {duration_text}
🗺 <b>سرور انتخابی:</b> {escape(server_name)}
💵 <b>مبلغ:</b> {plan.price:,} تومان
🏦 <b>موجودی کیف پول شما:</b> {user.wallet_balance:,} تومان

روش پرداخت مورد نظر را انتخاب کنید:"""

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 پرداخت آنلاین", callback_data=f"pay_online:{plan.id}:{slot_num}")
    builder.button(text="🏦 پرداخت از کیف پول (آنی)", callback_data=f"pay_wallet:{plan.id}:{slot_num}")
    builder.button(text="💳 کارت به کارت (دستی)", callback_data=f"pay_card:{plan.id}:{slot_num}")
    builder.button(text="🔙 بازگشت به لیست پلن‌ها", callback_data=f"buy_pick_loc:{slot_num}")
    builder.adjust(1)

    await safe_edit_or_reply(callback, invoice_text, reply_markup=builder.as_markup())


# ============================================================================
# CHECKOUT ACTIONS (WALLET, ONLINE, CARD-TO-CARD)
# ============================================================================

@router.callback_query(F.data.startswith("pay_wallet:"), StateFilter("*"))
async def handle_pay_wallet(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Instant checkout via user's wallet balance."""
    await callback.answer()
    _, plan_id_str, slot_num_str = callback.data.split(":")
    plan = await PlansRepository(session).get(int(plan_id_str))
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    slot_num = int(slot_num_str)

    if not plan or not user or slot_num not in SLOT_CONFIGS:
        return

    if user.wallet_balance < plan.price:
        await callback.message.answer(
            f"❌ موجودی کیف پول کافی نیست.\n"
            f"مبلغ: {plan.price:,} تومان | موجودی: {user.wallet_balance:,} تومان\n\n"
            f"جهت خرید می‌توانید از گزینه «کارت به کارت» یا «پرداخت آنلاین» استفاده کنید."
        )
        return

    await callback.message.answer("⚙️ در حال فعال‌سازی اشتراک دی‌ان‌اس شما...")

    now = datetime.now(timezone.utc)
    target_slot = SLOT_CONFIGS[slot_num]

    # Check for existing active subscription
    stmt = select(VPNService).where(VPNService.user_id == user.id, VPNService.status == "active").limit(1)
    res = await session.execute(stmt)
    current_sub = res.scalars().first()

    if not current_sub:
        expire_at = now + timedelta(hours=plan.duration_hours)
        unique_name = f"tg-user-{user.telegram_id}-{secrets.token_hex(4)}|default|{slot_num}"
        active_sub = VPNService(
            user_id=user.id,
            plan_id=plan.id,
            controld_device_id=target_slot["device_id"],
            config_link="sdns://placeholder",
            subscription_link="sdns://placeholder",
            username=unique_name,
            expire_at=expire_at,
            status="active",
        )
        session.add(active_sub)
    else:
        base_time = current_sub.expire_at if current_sub.expire_at > now else now
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)
        expire_at = base_time + timedelta(hours=plan.duration_hours)

        old_device = current_sub.controld_device_id
        if old_device and old_device != target_slot["device_id"] and current_sub.authorized_ip:
            cd = ControlDService(settings)
            await cd.deauthorize_ip(old_device, current_sub.authorized_ip)
            await cd.authorize_ip(target_slot["device_id"], current_sub.authorized_ip)

        current_sub.expire_at = expire_at
        current_sub.plan_id = plan.id
        current_sub.controld_device_id = target_slot["device_id"]
        current_sub.status = "active"
        active_sub = current_sub

    user.wallet_balance -= plan.price
    await session.commit()
    await state.clear()

    from bot.utils.messages import send_dns_delivery_card

    await send_dns_delivery_card(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        session=session,
        service=active_sub,
        title_prefix="✅ <b>اشتراک DNS شما با موفقیت فعال شد!</b>",
        ipv4_primary=target_slot["dns_primary"],
        ipv4_secondary=target_slot["dns_secondary"],
        service_display="کل ترافیک اینترنت (Default)",
        country_display=target_slot["name"],
        delay_seconds=7200,
    )


@router.callback_query(F.data.startswith("pay_card:"), StateFilter("*"))
async def handle_pay_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Manual card-to-card checkout with receipt upload."""
    await callback.answer()
    _, plan_id_str, slot_num_str = callback.data.split(":")
    plan = await PlansRepository(session).get(int(plan_id_str))
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)

    if not plan or not user:
        return

    order, payment = await OrderService(session, settings).create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=f"dns_user_{user.telegram_id}|default|{slot_num_str}",
    )
    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(order_id=order.id, payment_id=payment.id)

    app_settings = AppSettingsService(session)
    card_number = await app_settings.get_payment_card_number()
    card_holder = await app_settings.get_payment_card_holder()

    await callback.message.answer(
        f"""💳 <b>پرداخت کارت به کارت</b>

مبلغ قابل پرداخت: <b>{plan.price:,} تومان</b>

شماره کارت:
<code>{card_number or 'ثبت نشده'}</code>

به نام: <b>{card_holder or 'ثبت نشده'}</b>

📸 لطفاً پس از واریز، تصویر رسید را همینجا ارسال کنید:"""
    )


@router.callback_query(F.data.startswith("pay_online:"), StateFilter("*"))
async def handle_pay_online(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Paystar online gateway checkout."""
    await callback.answer()
    _, plan_id_str, slot_num_str = callback.data.split(":")
    plan = await PlansRepository(session).get(int(plan_id_str))
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)

    if not plan or not user:
        return

    order, payment = await OrderService(session, settings).create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=f"dns_user_{user.telegram_id}|default|{slot_num_str}",
        payment_method="paystar",
        commit=False,
    )

    token = await PaystarService().create_payment(
        amount_toman=payment.amount,
        order_id=order.tracking_code,
        callback_url=f"{settings.public_web_base_url}/paystar/callback",
    )
    if not token:
        await session.rollback()
        await callback.message.answer("❌ ساخت درگاه بانکی ناموفق بود. لطفاً روش دیگری انتخاب کنید.")
        return

    payment.token = token
    payment.method = "paystar"
    payment.status = PaymentStatus.PENDING.value
    await session.commit()
    await state.clear()

    redirect_url = f"{settings.public_web_base_url}/paystar/redirect?token={token}"
    await callback.message.answer(
        f"💳 مبلغ: <b>{plan.price:,} تومان</b>\n"
        f"کد سفارش: <code>{order.tracking_code}</code>\n\n"
        "⚠️ پیش از ورود به درگاه، فیلترشکن خود را خاموش کنید.",
        reply_markup=paystar_payment_keyboard(redirect_url),
    )


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Handles receipt image submission for card-to-card orders."""
    data = await state.get_data()
    order = await OrdersRepository(session).get_with_details(int(data.get("order_id") or 0))
    payment = await PaymentsRepository(session).get(int(data.get("payment_id") or 0))

    if not order or not payment:
        await state.clear()
        await message.answer("سفارش پیدا نشد.", reply_markup=main_menu_keyboard())
        return

    receipt_file_id = message.photo[-1].file_id
    await PaymentService(session, VPNPanelService(), settings).attach_receipt(payment, receipt_file_id)
    await state.clear()
    await message.answer("✅ رسید شما با موفقیت ثبت شد و در انتظار تایید مدیریت است.")

    from bot.notifications import notify_admins_order_payment
    await notify_admins_order_payment(
        bot=message.bot,
        session=session,
        settings=settings,
        payment=payment,
        order=order,
        receipt_file_id=receipt_file_id,
    )


# ============================================================================
# MANUAL IP REGISTRATION (WITH STRICT IRAN ANTI-VPN CHECK)
# ============================================================================

@router.callback_query(F.data.startswith("manual_ip_reg:"), StateFilter("*"))
async def handle_manual_ip_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    device_id = callback.data.split(":")[1]
    await state.set_state(BuyStates.waiting_manual_ip)
    await state.update_data(device_id=device_id)
    await callback.message.answer("لطفاً IP خود را وارد نمایید.\n برای مشاهده IP فعلی خود، روی لینک زیر کلیک کنید: \n\n  🌐 https://ipnumberia.com  \n\n ⚠️ نکته: حتماً VPN یا فیلترشکن خود را خاموش کنید و سپس IP نمایش‌داده‌شده را در بخش مربوطه وارد نمایید.")


@router.message(BuyStates.waiting_manual_ip, F.text)
async def process_manual_ip(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user_ip = message.text.strip()
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", user_ip):
        await message.answer("❌ فرمت آی‌پی نامعتبر است. مثال معتبر: `5.200.12.1`")
        return

    # 🛡️ STRICT IRAN CHECK
    from app.services.vpn_detector import verify_user_ip
    ip_check = await verify_user_ip(user_ip)
    if not ip_check.is_iran:
        await message.answer(
            f"⚠️ <b>خطا: فیلترشکن شما روشن است یا آی‌پی غیرایرانی وارد شده!</b>\n\n"
            f"🌐 <b>آی‌پی بررسی‌شده:</b> <code>{escape(user_ip)}</code>\n"
            f"🗺 <b>کشور شناسایی‌شده:</b> {escape(ip_check.country)} ({escape(ip_check.country_code)})\n"
            f"📡 <b>ارائه‌دهنده:</b> {escape(ip_check.isp)}\n\n"
            f"❌ <b>ثبت آی‌پی فقط برای اینترنت داخل ایران مجاز است.</b>\n"
            f"لطفاً فیلترشکن را خاموش کرده و آی‌پی واقعی اینترنت ایران خود را ارسال کنید.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    device_id = data.get("device_id")
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id) if message.from_user else None

    if not user or not device_id:
        await state.clear()
        await message.answer("❌ خطای سیستمی. لطفاً مجدداً تلاش کنید.")
        return

    stmt = (
        select(VPNService)
        .where(
            VPNService.user_id == user.id,
            VPNService.controld_device_id == device_id,
            VPNService.status != "disabled",
            VPNService.expire_at > datetime.now(timezone.utc),
        )
        .order_by(VPNService.expire_at.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    service = res.scalars().first()

    if not service:
        await state.clear()
        await message.answer("❌ اشتراک فعالی برای این دستگاه یافت نشد.")
        return

    if await update_device_ip_safe(session, service, user_ip):
        await state.clear()
        await message.answer(
            f"✅ آی‌پی <code>{user_ip}</code> با موفقیت برای دستگاه شما ثبت شد.",
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ خطا در ثبت آی‌پی در پنل. لطفاً مجدداً تلاش کنید.")
