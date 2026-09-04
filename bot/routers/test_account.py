# bot/routers/test_account.py
from __future__ import annotations

import uuid
import secrets
from datetime import datetime, timezone, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SLOT_CONFIGS, Settings
from app.models import IPAuthToken, VPNService, VPNServiceStatus
from app.repositories.users import UsersRepository
from app.utils.formatting import calculate_remaining_time_fa, format_datetime_fa
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.routers.services import create_secure_ip_update_keyboard
from bot.utils.auto_clean import schedule_message_deletion
from bot.utils.ui import safe_edit_or_reply

router = Router(name="test_account")


async def get_active_test_service(session: AsyncSession, user_id: int, now: datetime) -> VPNService | None:
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
    res = await session.execute(stmt)
    return res.scalars().first()


async def get_latest_test_service(session: AsyncSession, user_id: int) -> VPNService | None:
    stmt = (
        select(VPNService)
        .where(
            VPNService.user_id == user_id,
            VPNService.is_test_account.is_(True),
        )
        .order_by(VPNService.created_at.desc(), VPNService.id.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalars().first()


@router.message(F.text == texts.BTN_TEST_ACCOUNT)
@router.callback_query(F.data == "get_test_account", StateFilter("*"))
async def handle_get_test_account(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Entrypoint for free 2-hour DNS trial."""
    if isinstance(event, CallbackQuery):
        await event.answer()

    await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    user = await UsersRepository(session).get_by_telegram_id(user_id)
    if not user:
        await event.answer("ابتدا /start را ارسال کنید.")
        return

    now = datetime.now(timezone.utc).replace(microsecond=0)
    
    # Validation: already has active test?
    active_test = await get_active_test_service(session, user.id, now)
    if active_test:
        text = (
            "⚠️ شما در حال حاضر یک اکانت تست فعال دارید.\n\n"
            f"👤 نام دستگاه: <code>{escape(active_test.username.split('|')[0])}</code>\n"
            f"🗓 تاریخ انقضا: {format_datetime_fa(active_test.expire_at)}\n"
            f"⏳ زمان باقی‌مانده: {calculate_remaining_time_fa(active_test.expire_at)}"
        )
        await safe_edit_or_reply(event, text, reply_markup=main_menu_keyboard())
        return

    # Validation: used test in the past?
    if await get_latest_test_service(session, user.id):
        await safe_edit_or_reply(
            event,
            "❌ شما قبلاً از اکانت تست رایگان استفاده کرده‌اید و امکان دریافت مجدد وجود ندارد.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Render Germany (1) and Turkey (5) for test accounts
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🇩🇪 آلمان (فرانکفورت) — پینگ پایدار ⚡",
        callback_data="test_loc:1",
        style="primary"
    )
    builder.button(
        text="🇹🇷 ترکیه (استانبول) — کمترین پینگ گیمینگ 🚀",
        callback_data="test_loc:5",
        style="primary"
    )
    builder.button(
        text="🔙 بازگشت به منو",
        callback_data="buy_back_to_menu",
        style="danger"
    )
    builder.adjust(1)

    prompt = """🎁 <b>دریافت اکانت تست ۲ ساعته رایگان</b>

🗺 <b>لطفاً لوکیشن سرور مورد نظر خود را انتخاب کنید:</b>

<blockquote>⚡ <b>مقایسه سرورهای تست:</b>
🇩🇪 <b>آلمان:</b> ترافیک پایدار و سرعت دانلود عالی
🇹🇷 <b>ترکیه:</b> پایین‌ترین پینگ و تاخیر برای بازی‌های آنلاین</blockquote>

💡 <i>هر کاربر تلگرام تنها یک بار امکان دریافت اکانت تست رایگان را دارد.</i>"""

    await safe_edit_or_reply(event, prompt, reply_markup=builder.as_markup())
    

@router.callback_query(F.data.startswith("test_loc:"), StateFilter("*"))
async def handle_test_loc_selection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Provisions the 2-hour trial on the chosen location."""
    await callback.answer()
    if not callback.message or not callback.from_user:
        return

    slot_num = int(callback.data.split(":")[1])
    if slot_num not in SLOT_CONFIGS:
        await callback.message.answer("❌ اسلات انتخاب شده معتبر نیست.")
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if not user:
        return

    now = datetime.now(timezone.utc).replace(microsecond=0)
    if await get_active_test_service(session, user.id, now) or await get_latest_test_service(session, user.id):
        await callback.message.edit_text("❌ امکان دریافت اکانت تست وجود ندارد.", reply_markup=None)
        return

    await callback.message.edit_text("⚙️ در حال ساخت دی‌ان‌اس تست ۲ ساعته شما...", reply_markup=None)

    config = SLOT_CONFIGS[slot_num]
    expire_at = now + timedelta(hours=2)
    device_name = f"tg-test-{user.telegram_id}-{secrets.token_hex(4)}|default|{slot_num}"

    test_sub = VPNService(
        user_id=user.id,
        plan_id=None,
        controld_device_id=config["device_id"],
        config_link="sdns://placeholder",
        subscription_link="sdns://placeholder",
        username=device_name,
        expire_at=expire_at,
        status="active",
        is_test_account=True,
    )
    session.add(test_sub)
    await session.flush()

    # Clear old tokens & issue a secure auth token
    await session.execute(delete(IPAuthToken).where(IPAuthToken.service_id == test_sub.id))
    session.add(IPAuthToken(
        token=uuid.uuid4().hex,
        service_id=test_sub.id,
        expires_at=now + timedelta(minutes=10),
        is_used=False,
    ))
    
    await session.commit()
    await state.clear()

    # ✅ UNIFIED DUAL-DNS DELIVERY CARD FOR TEST ACCOUNT:
    from bot.utils.messages import send_dns_delivery_card

    await send_dns_delivery_card(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        session=session,
        service=test_sub,
        title_prefix="🎁 <b>اکانت تست ۲ ساعته رایگان شما فعال شد!</b>",
        ipv4_primary=config["dns_primary"],
        ipv4_secondary=config["dns_secondary"],
        service_display="کل ترافیک اینترنت (Default)",
        country_display=config["name"],
        delay_seconds=7200,
    )