# bot/routers/admin.py
from __future__ import annotations

import hmac
import hashlib
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo
import jdatetime
import httpx
from sqlalchemy.orm import joinedload
import structlog

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as TelegramUser, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings, SLOT_CONFIGS
from app.models import Order, User, VPNService, IPAuthToken
from app.repositories.plans import PlansRepository
from app.repositories.reports import ReportsRepository
from app.repositories.users import UsersRepository
from app.services.payment_service import ApprovedPaymentResult
from app.utils.formatting import format_money
from app.models import User  
from app.database import async_session_maker
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from aiogram.types import Message, CallbackQuery
import asyncio
from aiogram import Router, F
from sqlalchemy import or_
from bot.keyboards.admin import AdminServiceCallback, service_detail_keyboard

from sqlalchemy import func
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.routers.services import is_service_active

PAGE_SIZE = 10


def calculate_remaining_time_fa(expire_at) -> str:
    if not expire_at:
        return "۳۰ روز"
    from datetime import datetime, timezone
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
    return f"{int(total_seconds // 60)} دقیقه"

from app.services.settings_service import (
    AppSettingsService,
    SETTING_DEFINITIONS,
    SETTING_DEFINITION_BY_KEY,
    SUPPORT_USERNAME,
    TEACHING_VIDEO_LINK,
)
from app.utils.formatting import format_money, calculate_remaining_time_fa
from bot import texts
from bot.keyboards.admin import (
    AdminActionCallback,
    AdminSettingCallback,
    AdminUserCallback,
    admin_main_keyboard,
    admin_sales_keyboard,
    admin_users_affiliate_keyboard,
    admin_payments_keyboard,
    admin_services_keyboard,
    admin_communications_keyboard,
    admin_settings_keyboard,
    bot_settings_keyboard,
    pending_payments_keyboard,
    services_admin_keyboard,
    setting_edit_keyboard,
    plans_management_keyboard,
    test_accounts_keyboard,
    users_admin_keyboard,
    user_detail_keyboard,
)
from bot.keyboards.main_menu import main_menu_keyboard
from bot.states.admin import AdminSettingsStates, AdminSearchStates, AdminAddPlanStates

# Sub-routers
from bot.routers import admin_orders, admin_plans
from bot.keyboards.admin import (
    AdminPlanCallback,
    plan_delete_confirm_keyboard,
    plan_detail_keyboard,
    plans_management_keyboard,
)
from sqlalchemy import update
from app.models import ConfigInventory, AffiliateCommission, Payment, Order


router = Router(name="admin")
router.include_router(admin_orders.router)
router.include_router(admin_plans.router)

logger = structlog.get_logger(__name__)
WEB_SERVER_BASE_URL = get_settings().public_web_base_url


def _approved_message(
    result: ApprovedPaymentResult,
    expire_at: datetime | None = None,
    ipv4_primary: str = "76.76.2.162",
    ipv4_secondary: str = "76.76.10.162",
    custom_username: str | None = None,
) -> str:
    if result.waiting_inventory:
        return "⏳ <b>پرداخت تایید شد.</b> پشتیبانی به‌زودی اطلاعات اشتراک شما را ارسال می‌کند."

    target_expire = expire_at or result.new_expire_at
    try:
        if target_expire and target_expire.tzinfo is None:
            target_expire = target_expire.replace(tzinfo=timezone.utc)
        tehran_expire = target_expire.astimezone(ZoneInfo("Asia/Tehran"))
        expire_str = jdatetime.datetime.fromgregorian(datetime=tehran_expire.replace(tzinfo=None)).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        expire_str = target_expire.strftime("%Y-%m-%d %H:%M:%S") if target_expire else "-"

    duration_text = calculate_remaining_time_fa(target_expire)
    settings = get_settings()

    agh_section = f"""
🔹 Primary: <code>{escape(settings.adguard_primary_dns or 'تنظیم نشده')}</code>
🔹 Secondary: <code>{escape(settings.adguard_secondary_dns or 'تنظیم نشده')}</code>"""
    if settings.adguard_doh_url:
        agh_section += f"\n🌐 DoH: <code>{escape(settings.adguard_doh_url)}</code>"

    return f"""✅ <b>پرداخت شما تایید و اشتراک فعال شد!</b>

🔹 <b>تاریخ انقضاء:</b> <code>{escape(expire_str)}</code>
🔷 <b>زمان باقی‌مانده:</b> {escape(duration_text)}
━━━━━━━━━━━━━━━━━━━━━

{agh_section}

🔹 Primary: <code>{escape(ipv4_primary)}</code>
🔹 Secondary: <code>{escape(ipv4_secondary)}</code>
━━━━━━━━━━━━━━━━━━━━━
📋 <b>مراحل فعال‌سازی:</b>
1️⃣ فیلترشکن و پروکسی تلگرام خود را خاموش کنید.
2️⃣ روی دکمه <b>«ثبت آی‌پی اتوماتیک»</b> زیر کلیک کنید."""


async def get_controld_device_ips(device_id: str, settings: Settings) -> dict:
    for config in SLOT_CONFIGS.values():
        if config["device_id"] == device_id:
            return {"ipv4_primary": config["dns_primary"], "ipv4_secondary": config["dns_secondary"]}
    return {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}


async def _is_admin(telegram_id: int | None, session: AsyncSession, settings: Settings) -> bool:
    if telegram_id is None:
        return False
    if settings.root_admin_telegram_id and telegram_id == settings.root_admin_telegram_id:
        return True
    if telegram_id in settings.admin_ids:
        return True
    user = await UsersRepository(session).get_by_telegram_id(telegram_id)
    return bool(user and user.is_admin)


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        await message.answer("⛔ شما دسترسی مدیریت ندارید.")
        return
    await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())


@router.callback_query(AdminActionCallback.filter())
async def admin_action_navigation(
    callback: CallbackQuery,
    callback_data: AdminActionCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    await callback.answer()
    action = callback_data.action
    await state.clear()

    menus = {
        "panel": (texts.ADMIN_PANEL_TEXT, admin_main_keyboard()),
        "cat_sales": ("📦 فروش و تعرفه‌ها", admin_sales_keyboard()),
        "cat_users": ("👥 کاربران و زیرمجموعه‌ها", admin_users_affiliate_keyboard()),
        "cat_payments": ("💳 پرداخت‌ها و کیف پول", admin_payments_keyboard()),
        "cat_comms": ("📣 ارتباطات", admin_communications_keyboard()),
        "cat_settings": ("⚙️ تنظیمات", admin_settings_keyboard()),
    }

    if action in menus:
        text, kb = menus[action]
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "back":
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(is_admin=True))
        return

    if action == "plans":
        plans = await PlansRepository(session).list_all()
        await callback.message.edit_text("📦 مدیریت تعرفه‌ها:", reply_markup=plans_management_keyboard(plans))
        return

    if action == "add_plan":
        from bot.states.admin import AdminAddPlanStates
        await state.set_state(AdminAddPlanStates.title)
        await callback.message.answer("عنوان تعرفه را ارسال کنید:")
        return

    if action == "settings":
        values = await AppSettingsService(session).get_all_settings()
        await callback.message.edit_text("⚙️ تنظیمات ربات:", reply_markup=bot_settings_keyboard())
        return

    if action == "admin_broadcast":
        from bot.states.admin import AdminBroadcastStates
        await state.set_state(AdminBroadcastStates.text)
        await callback.message.edit_text("لطفاً پیام خود را ارسال کنید :\n\n❌ برای لغو، کلمه /cancel را ارسال کنید.")
        return
        
    if action == "tutorials_admin":
        builder = InlineKeyboardBuilder()
        builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_comms"))
        builder.adjust(1)
        await callback.message.edit_text(
            "📚 <b>مدیریت آموزش‌ها</b>\n\nدر نسخه فعلی، آموزش‌ها به صورت استاتیک در فایل‌های کیبورد ربات (`bot/keyboards/tutorials.py`) تعریف شده‌اند و برای تغییر محتوای آن‌ها باید کدهای این بخش را ویرایش کنید.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return
        
    if action == "support_admin":
        support_username = await AppSettingsService(session).get_support_username()
        support_text = f"@{escape(support_username)}" if support_username else "ثبت نشده"
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ ویرایش آیدی پشتیبانی", callback_data=AdminSettingCallback(action="edit", key=SUPPORT_USERNAME))
        builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_comms"))
        builder.adjust(1)
        await callback.message.edit_text(
            f"☎️ <b>مدیریت پشتیبانی</b>\n\nآیدی پشتیبانی فعلی ربات: {support_text}\n\nبرای تغییر آیدی پشتیبانی روی دکمه زیر کلیک کنید:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    if action == "video_link_admin":
        link = await AppSettingsService(session).get_teaching_video_link()
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ ویرایش لینک", callback_data=AdminSettingCallback(action="edit", key=TEACHING_VIDEO_LINK))
        builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_comms"))
        builder.adjust(1)
        await callback.message.edit_text(
            f"🎥 <b>لینک ویدیو آموزشی:</b>\n<code>{escape(link or 'ثبت نشده')}</code>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    if action == "users":
        res = await session.execute(select(User).order_by(User.id.desc()).limit(30))
        users = list(res.scalars().all())
        await callback.message.edit_text("👥 لیست جدیدترین کاربران:", reply_markup=users_admin_keyboard(users))
        return

    if action == "services":
        await show_admin_services_list(callback, page=0, session=session)
        return

    if action == "test_accounts":
        from app.models import TestAccount
        res = await session.execute(select(TestAccount))
        accounts = list(res.scalars().all())
        await callback.message.edit_text("🔑 مدیریت اکانت‌های تست:", reply_markup=test_accounts_keyboard(accounts))
        return

    if action == "payments":
        from app.models import Payment
        from sqlalchemy.orm import joinedload
        res = await session.execute(
            select(Payment)
            .options(joinedload(Payment.order))
            .where(Payment.status == "pending")
        )
        payments = list(res.scalars().all())
        await callback.message.edit_text("💳 پرداخت‌های در انتظار تایید:", reply_markup=pending_payments_keyboard(payments))
        return

    if action == "affiliate":
        from bot.keyboards.admin import affiliate_management_keyboard
        await callback.message.edit_text("👥 سیستم زیرمجموعه‌گیری:", reply_markup=affiliate_management_keyboard())
        return

    if action == "open_channels_menu":
        await state.clear()
        # Import the menu generator from the external file and trigger it
        from bot.routers.mandatory_channels import cmd_admin_channels
        await cmd_admin_channels(callback, session, settings)
        return
    
    if action == "orders":
            await _show_recent_orders(callback, session)
            return
    
    if action == "sales_report":
        await _show_sales_report(callback, session)
        return


    if action == "cat_services":
        await callback.message.edit_text(
            "🛍 <b>بخش مدیریت اشتراک‌های DNS:</b>", 
            reply_markup=admin_services_keyboard(), 
            parse_mode="HTML"
        )
    return

    
    

@router.message(Command("teaching_video_link"), StateFilter("*"))
async def cmd_teaching_video_link(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        return
    app_settings = AppSettingsService(session)
    args = (message.text or "").split(maxsplit=1)
    if len(args) == 1:
        current = await app_settings.get_teaching_video_link()
        await message.answer(f"🎥 لینک فعلی: <code>{escape(current or 'ثبت نشده')}</code>\n\nبرای تغییر:\n<code>/teaching_video_link https://t.me/...</code>")
        return

    val = args[1].strip()
    if val.lower() in {"remove", "delete", "clear", "-"}:
        await app_settings.set_setting(TEACHING_VIDEO_LINK, "")
        await session.commit()
        await message.answer("🗑 لینک ویدیو حذف شد.")
        return

    if not val.startswith(("http://", "https://", "t.me/")):
        await message.answer("❌ لینک نامعتبر است.")
        return

    new_link = f"https://{val}" if val.startswith("t.me/") else val
    await app_settings.set_setting(TEACHING_VIDEO_LINK, new_link)
    await session.commit()
    await message.answer(f"✅ لینک ذخیره شد:\n<code>{escape(new_link)}</code>", parse_mode="HTML")


@router.message(Command("consolidate_services"), StateFilter("*"))
async def cmd_consolidate_services(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        return

    msg = await message.answer("⚙️ در حال یکپارچه‌سازی اشتراک‌ها...")
    rows = await session.execute(
        select(VPNService.user_id)
        .where(VPNService.is_test_account == False)
        .group_by(VPNService.user_id)
        .having(func.count(VPNService.id) > 1)
    )
    user_ids = rows.scalars().all()
    cleaned = 0
    now = datetime.now(timezone.utc)

    for uid in user_ids:
        res = await session.execute(
            select(VPNService).where(VPNService.user_id == uid, VPNService.is_test_account == False).order_by(VPNService.expire_at.desc())
        )
        services = list(res.scalars().all())
        if len(services) > 1:
            primary = services[0]
            max_exp = max(s.expire_at for s in services)
            if max_exp.tzinfo is None:
                max_exp = max_exp.replace(tzinfo=timezone.utc)
            primary.expire_at = max_exp
            if max_exp > now:
                primary.status = "active"

            duplicate_ids = [s.id for s in services[1:]]
            await session.execute(delete(VPNService).where(VPNService.id.in_(duplicate_ids)))
            cleaned += len(duplicate_ids)

    await session.commit()
    await msg.edit_text(f"✅ یکپارچه‌سازی انجام شد. <code>{cleaned}</code> رکورد تکراری حذف گردید.", parse_mode="HTML")


@router.message(Command("reset_tests"), StateFilter("*"))
async def cmd_reset_all_tests(message: Message, session: AsyncSession, settings: Settings) -> None:
    # 1. Security Check: Only admins can run this
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        return

    msg = await message.answer("⏳ در حال پاکسازی اطلاعات تست تمام کاربران...")
    
    try:
        from app.models import TestAccountClaim, VPNService
        from sqlalchemy import delete
        
        # 2. Delete all claims so everyone can fetch a new test account
        await session.execute(delete(TestAccountClaim))
        
        # 3. Delete all currently active test/trial VPN services
        await session.execute(
            delete(VPNService).where(VPNService.is_test_account == True)
        )
        
        # 4. Save changes
        await session.commit()
        
        await msg.edit_text(
            "✅ <b>وضعیت اکانت تست برای تمام کاربران با موفقیت ریست شد.</b>\n\n"
            "اکنون همه کاربران می‌توانند مجدداً از منوی اصلی اکانت تست دریافت کنند.", 
            parse_mode="HTML"
        )
    except Exception as exc:
        await session.rollback()
        logger.error("failed_to_reset_all_tests", error=str(exc))
        await msg.edit_text(f"❌ خطا در ریست کردن اکانت‌های تست:\n<code>{str(exc)}</code>", parse_mode="HTML")

from bot.states.admin import AdminBroadcastStates

@router.message(AdminBroadcastStates.text)
async def execute_broadcast(message: Message, state: FSMContext):
    if message.text and message.text.lower() == '/cancel':
        await state.clear()
        return await message.answer("عملیات ارسال پیام همگانی لغو شد.")

    await state.clear()
    status_msg = await message.answer("⏳ در حال ارسال پیام همگانی... لطفاً ربات را متوقف نکنید.")

    success_count = 0
    fail_count = 0

    # Fetch all users from the database
    async with async_session_maker() as session:
        stmt = select(User.telegram_id).where(User.telegram_id.is_not(None))
        res = await session.execute(stmt)
        user_ids = res.scalars().all()

    # Loop through users and send
    for telegram_id in user_ids:
        try:
            # copy_to clones the exact message (including photos, videos, buttons)
            await message.copy_to(chat_id=telegram_id)
            success_count += 1
            
            # CRITICAL: 0.05s delay prevents Telegram from blocking your bot for spamming
            await asyncio.sleep(0.05) 
            
        except Exception:
            # Fails if the user blocked the bot or deleted their account
            fail_count += 1

    await status_msg.edit_text(
        f"✅ پیام همگانی با موفقیت به پایان رسید!\n\n"
        f"🟢 ارسال موفق: {success_count} کاربر\n"
        f"🔴 ارسال ناموفق (ربات بلاک شده): {fail_count} کاربر"
    )

    
class AdminUserSearchState(StatesGroup):
    waiting_for_query = State()

# 1. Trigger the search prompt when the button is clicked
@router.callback_query(AdminUserCallback.filter(F.action == "search"))
async def prompt_user_search(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔎 <b>جستجوی کاربر</b>\n\n"
        "لطفاً یکی از موارد زیر را ارسال کنید:\n"
        "▫️ آیدی عددی (Telegram ID)\n"
        "▫️ یوزرنیم تلگرام (بدون @)\n"
        "▫️ نام کاربر\n\n"
        "❌ برای لغو، کلمه `/cancel` را ارسال کنید.",
        parse_mode="HTML"
    )
    await state.set_state(AdminUserSearchState.waiting_for_query)
    await callback.answer()

# 2. Receive the text and query the database
@router.message(AdminUserSearchState.waiting_for_query, F.text)
async def execute_user_search(message: Message, state: FSMContext, session: AsyncSession):
    query = message.text.strip()
    
    if query.lower() == '/cancel':
        await state.clear()
        return await message.answer("عملیات جستجوی کاربر لغو شد.")

    wait_msg = await message.answer("⏳ در حال جستجو در دیتابیس...")
    await state.clear()

    # Build the dynamic SQLAlchemy query
    stmt = select(User)
    
    if query.isdigit():
        # If input is numbers, search by Telegram ID or Internal DB ID
        stmt = stmt.where(
            or_(
                User.telegram_id == int(query),
                User.id == int(query)
            )
        )
    else:
        # If input is text, search by Username or First Name
        clean_query = query.removeprefix("@")
        stmt = stmt.where(
            or_(
                User.telegram_username.ilike(f"%{clean_query}%"),
                User.first_name.ilike(f"%{clean_query}%")
            )
        )

    # Limit to 30 results to prevent massive keyboard payloads
    stmt = stmt.limit(30)
    res = await session.execute(stmt)
    users = list(res.scalars().all())

    await wait_msg.delete()

    if not users:
        return await message.answer(
            f"❌ هیچ کاربری با مشخصات «{escape(query)}» یافت نشد.",
            parse_mode="HTML"
        )

    await message.answer(
        f"🔎 نتایج جستجو برای «{escape(query)}»:\n(تعداد: {len(users)} کاربر)",
        reply_markup=users_admin_keyboard(users),
        parse_mode="HTML"
    )


class AdminServiceSearchState(StatesGroup):
    waiting_for_query = State()

@router.callback_query(AdminServiceCallback.filter())
async def admin_service_callback_handler(callback: CallbackQuery, callback_data: AdminServiceCallback, state: FSMContext, session: AsyncSession):
    action = callback_data.action
    service_id = callback_data.service_id
    await callback.answer()
    
    if action == "search":
        await callback.message.edit_text(
            "🔎 <b>جستجوی اشتراک</b>\n\nلطفاً آی‌پی (IP) یا نام کاربری دستگاه را ارسال کنید:\n❌ لغو: `/cancel`",
            parse_mode="HTML"
        )
        await state.set_state(AdminServiceSearchState.waiting_for_query)
        return
        
    if action == "detail":
        stmt = select(VPNService).options(joinedload(VPNService.user)).where(VPNService.id == service_id)
        res = await session.execute(stmt)
        service = res.scalars().first()
        
        if not service:
            return await callback.message.edit_text("❌ اشتراک یافت نشد.")
            
        status_fa = "🟢 فعال" if service.status == "active" else "🔴 غیرفعال"
        ip = service.authorized_ip or "ثبت نشده"
        owner = f"@{service.user.telegram_username}" if service.user and service.user.telegram_username else str(service.user.telegram_id if service.user else "نامشخص")
        
        text = (
            f"🛍 <b>جزئیات اشتراک DNS</b>\n\n"
            f"👤 <b>نام دستگاه:</b> <code>{escape(service.username or 'ندارد')}</code>\n"
            f"👑 <b>مالک:</b> {escape(owner)}\n"
            f"📌 <b>وضعیت:</b> {status_fa}\n"
            f"🌐 <b>آی‌پی فعال:</b> <code>{escape(ip)}</code>\n"
            f"🆔 <b>شناسه سرور:</b> <code>{escape(service.controld_device_id or 'ندارد')}</code>"
        )
        await callback.message.edit_text(text, reply_markup=service_detail_keyboard(service), parse_mode="HTML")
        return

@router.message(AdminServiceSearchState.waiting_for_query, F.text)
async def execute_service_search(message: Message, state: FSMContext, session: AsyncSession):
    query = message.text.strip()
    if query.lower() == '/cancel':
        await state.clear()
        return await message.answer("لغو شد.")
        
    await state.clear()
    wait_msg = await message.answer("⏳ در حال جستجو...")
    
    from sqlalchemy import or_
    stmt = select(VPNService).where(
        or_(
            VPNService.authorized_ip.ilike(f"%{query}%"),
            VPNService.username.ilike(f"%{query}%")
        )
    ).limit(30)
    res = await session.execute(stmt)
    services = list(res.scalars().all())
    
    await wait_msg.delete()
    if not services:
        return await message.answer("❌ اشتراکی یافت نشد.")
        
    await message.answer(f"🔎 نتایج برای «{escape(query)}»:", reply_markup=services_admin_keyboard(services))


async def show_admin_services_list(callback: CallbackQuery, page: int, session: AsyncSession):
    # 1. Total count
    count_stmt = select(func.count(VPNService.id)).where(VPNService.is_test_account == False)
    total_count = (await session.execute(count_stmt)).scalar() or 0
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    # 2. Fetch page items with user joined
    stmt = (
        select(VPNService)
        .options(joinedload(VPNService.user))
        .where(VPNService.is_test_account == False)
        .order_by(VPNService.id.desc())
        .offset(page * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    res = await session.execute(stmt)
    services = list(res.scalars().all())

    if not services:
        return await callback.message.edit_text(
            "🛍 هیچ اشتراک فعالی در دیتابیس یافت نشد.",
            reply_markup=admin_services_keyboard()
        )

    # 3. Format detailed text list
    lines = [f"🛍 <b>لیست اشتراک‌های DNS</b> (صفحه {page + 1} از {total_pages} | کل: {total_count})\n"]
    now = datetime.now(timezone.utc)
    
    for s in services:
        owner = f"@{s.user.telegram_username}" if s.user and s.user.telegram_username else str(s.user.telegram_id if s.user else "نامشخص")
        ip = s.authorized_ip if s.authorized_ip else "❌ ثبت نشده"
        
        # Safe inline activity check
        is_active = False
        if s.status != "disabled" and s.expire_at:
            exp = s.expire_at.replace(tzinfo=timezone.utc) if s.expire_at.tzinfo is None else s.expire_at
            is_active = exp > now
            
        status_emoji = "🟢 فعال" if is_active else "🔴 منقضی/غیرفعال"
        raw_name = (s.username or "نامشخص").split("|")[0].strip()
        
        lines.append(
            f"🆔 <b>شناسه:</b> <code>#{s.id}</code>\n"
            f"👤 <b>کاربر:</b> {escape(owner)}\n"
            f"📱 <b>دستگاه:</b> <code>{escape(raw_name)}</code>\n"
            f"🌐 <b>آی‌پی فعال:</b> <code>{escape(ip)}</code>\n"
            f"📌 <b>وضعیت:</b> {status_emoji}\n"
            f"────────────────────"
        )

    text_content = "\n".join(lines)

    # 4. Navigation buttons
    builder = InlineKeyboardBuilder()
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"admin_svc_page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"admin_svc_page:{page + 1}"))
    
    if nav_row:
        builder.row(*nav_row)
        
    builder.row(InlineKeyboardButton(text="↩️ بازگشت به منوی سرویس‌ها", callback_data=AdminActionCallback(action="cat_services").pack()))

    await callback.message.edit_text(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_svc_page:"), StateFilter("*"))
async def handle_admin_svc_page(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    
    # Extract the page number from callback data (e.g., "admin_svc_page:1" -> 1)
    try:
        page = int(callback.data.split(":")[1])
        await show_admin_services_list(callback, page=page, session=session)
    except Exception as e:
        await callback.message.answer(f"❌ خطا در تغییر صفحه: {str(e)}")


@router.callback_query(AdminUserCallback.filter())
async def admin_user_callback_handler(callback: CallbackQuery, callback_data: AdminUserCallback, state: FSMContext, session: AsyncSession):
    await callback.answer()
    action = callback_data.action
    user_id = callback_data.user_id
    
    if action == "search":
        # Handled by the search FSM we added earlier
        return

    if action == "detail":
        user = await session.get(User, user_id)
        if not user:
            return await callback.message.edit_text("❌ کاربر یافت نشد.")
        
        # Format user attributes safely
        username = f"@{user.telegram_username}" if user.telegram_username else "ندارد"
        name = user.first_name or "نامشخص"
        balance = getattr(user, 'wallet_balance', 0)
        is_admin_text = "بله ✅" if user.is_admin else "خیر ❌"
        
        text = (
            f"👤 <b>مدیریت و جزئیات کاربر</b>\n\n"
            f"🆔 <b>آیدی عددی:</b> <code>{user.telegram_id}</code>\n"
            f"🏷 <b>یوزرنیم:</b> {escape(username)}\n"
            f"📛 <b>نام:</b> {escape(name)}\n"
            f"💰 <b>موجودی کیف پول:</b> {balance:,.0f} تومان\n"
            f"👑 <b>دسترسی ادمین:</b> {is_admin_text}\n"
        )
        
        await callback.message.edit_text(
            text, 
            reply_markup=user_detail_keyboard(user, viewer_id=callback.from_user.id), 
            parse_mode="HTML"
        )
        return

from app.repositories.reports import ReportsRepository
from bot.keyboards.admin import AdminOrderCallback

async def _show_sales_report(callback: CallbackQuery, session: AsyncSession) -> None:
    report = await ReportsRepository(session).get_sales_report()
    text = (
        f"📈 <b>گزارش جامع فروش</b>\n\n"
        f"<b>💵 فروش</b>\n"
        f"امروز: <b>{format_money(report.today_sales)} تومان</b>\n"
        f"این هفته: <b>{format_money(report.week_sales)} تومان</b>\n"
        f"کل فروش: <b>{format_money(report.total_sales)} تومان</b>\n"
        f"سفارش‌های موفق: <b>{report.completed_orders_count}</b>\n\n"
        f"<b>🛍 اشتراک‌ها</b>\n"
        f"فعال: <b>{report.active_subscriptions_count}</b>\n"
        f"منقضی: <b>{report.expired_subscriptions_count}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_sales_keyboard(), parse_mode="HTML")


async def _show_recent_orders(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    limit = 8
    offset = page * limit
    result = await session.execute(
        select(Order)
        .order_by(Order.created_at.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    orders = list(result.scalars().all())
    has_next = len(orders) > limit
    if has_next:
        orders = orders[:limit]

    if not orders and page == 0:
        return await callback.message.edit_text("هنوز سفارشی ثبت نشده است.", reply_markup=admin_sales_keyboard())

    builder = InlineKeyboardBuilder()
    for order in orders:
        status_emoji = "🟢" if order.status == "completed" else "🔴" if order.status in ("expired", "canceled") else "🟡"
        builder.button(
            text=f"{status_emoji} {order.tracking_code} | {format_money(order.amount)}ت",
            callback_data=AdminOrderCallback(action="detail", order_id=order.id).pack(),
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=AdminOrderCallback(action="list", page=page - 1).pack()))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=AdminOrderCallback(action="list", page=page + 1).pack()))
    
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_sales").pack()))
    builder.adjust(1)

    await callback.message.edit_text(
        f"🧾 لیست سفارش‌ها و رزروها (صفحه {page + 1})\nبرای مدیریت هر سفارش روی آن کلیک کنید:",
        reply_markup=builder.as_markup()
    )

async def _show_sales_report(callback: CallbackQuery, session: AsyncSession) -> None:
    from app.repositories.reports import ReportsRepository
    from bot.keyboards.admin import admin_sales_keyboard
    from app.utils.formatting import format_money

    report = await ReportsRepository(session).get_sales_report()
    text = (
        f"📈 <b>گزارش جامع فروش</b>\n\n"
        f"<b>💵 فروش</b>\n"
        f"امروز: <b>{format_money(report.today_sales)} تومان</b>\n"
        f"این هفته: <b>{format_money(report.week_sales)} تومان</b>\n"
        f"کل فروش: <b>{format_money(report.total_sales)} تومان</b>\n"
        f"سفارش‌های موفق: <b>{report.completed_orders_count}</b>\n\n"
        f"<b>🛍 اشتراک‌ها</b>\n"
        f"فعال: <b>{report.active_subscriptions_count}</b>\n"
        f"منقضی: <b>{report.expired_subscriptions_count}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_sales_keyboard(), parse_mode="HTML")


async def _show_recent_orders(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    from app.models import Order
    from sqlalchemy import select
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.keyboards.admin import admin_sales_keyboard, AdminActionCallback
    from app.utils.formatting import format_money

    # Dynamically define callback to prevent import errors
    from aiogram.filters.callback_data import CallbackData
    class LocalAdminOrderCallback(CallbackData, prefix="adm_ord"):
        action: str
        order_id: int = 0
        page: int = 0

    limit = 8
    offset = page * limit
    result = await session.execute(
        select(Order)
        .order_by(Order.created_at.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    orders = list(result.scalars().all())
    has_next = len(orders) > limit
    if has_next:
        orders = orders[:limit]

    if not orders and page == 0:
        return await callback.message.edit_text("هنوز سفارشی ثبت نشده است.", reply_markup=admin_sales_keyboard())

    builder = InlineKeyboardBuilder()
    for order in orders:
        status_emoji = "🟢" if order.status == "completed" else "🔴" if order.status in ("expired", "canceled") else "🟡"
        builder.button(
            text=f"{status_emoji} {order.tracking_code} | {format_money(order.amount)}ت",
            callback_data=LocalAdminOrderCallback(action="detail", order_id=order.id).pack(),
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=LocalAdminOrderCallback(action="list", page=page - 1).pack()))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=LocalAdminOrderCallback(action="list", page=page + 1).pack()))
    
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_sales").pack()))
    builder.adjust(1)

    await callback.message.edit_text(
        f"🧾 لیست سفارش‌ها و رزروها (صفحه {page + 1})\nبرای مدیریت هر سفارش روی آن کلیک کنید:",
        reply_markup=builder.as_markup()
    )

from app.repositories.plans import PlansRepository
from bot.keyboards.admin import (
    AdminPlanCallback,
    plan_delete_confirm_keyboard,
    plan_detail_keyboard,
    plans_management_keyboard,
)
from sqlalchemy import update
from app.models import ConfigInventory, AffiliateCommission, Payment, Order, VPNService
from html import escape

# ---------------------------------------------------------
# 1. HELPER FUNCTIONS (Must be placed BEFORE the handler)
# ---------------------------------------------------------
async def _show_plans(callback: CallbackQuery, session: AsyncSession, prefix: str = "") -> None:
    plans = await PlansRepository(session).list_all()
    if not plans:
        text = f"{prefix}📦 <b>مدیریت تعرفه‌ها</b>\n\nهنوز تعرفه‌ای ثبت نشده است."
    else:
        lines = [f"{prefix}📦 <b>مدیریت تعرفه‌ها:</b>\n"]
        for idx, p in enumerate(plans, 1):
            status = "🟢 فعال" if p.is_active else "🔴 غیرفعال"
            price_val = getattr(p, "price", 0)
            lines.append(f"{idx}. <b>{escape(p.title)}</b> | {price_val:,} تومان | {status}")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=plans_management_keyboard(plans), parse_mode="HTML")


async def _show_plan_detail(callback: CallbackQuery, plan, session: AsyncSession) -> None:
    status = "🟢 فعال" if plan.is_active else "🔴 غیرفعال"
    desc = plan.description or "ندارد"
    price_val = getattr(plan, "price", 0)
    duration = f"{plan.duration_hours} ساعت" if hasattr(plan, "duration_hours") else "-"

    text = (
        f"📦 <b>جزئیات تعرفه</b>\n\n"
        f"🆔 <b>شناسه:</b> <code>#{plan.id}</code>\n"
        f"📌 <b>عنوان:</b> {escape(plan.title)}\n"
        f"💵 <b>قیمت:</b> {price_val:,} تومان\n"
        f"⏳ <b>مدت اعتبار:</b> {duration}\n"
        f"📝 <b>توضیحات:</b> {escape(desc)}\n"
        f"📊 <b>وضعیت:</b> {status}"
    )
    await callback.message.edit_text(text, reply_markup=plan_detail_keyboard(plan), parse_mode="HTML")


# ---------------------------------------------------------
# 2. MAIN HANDLER
# ---------------------------------------------------------
@router.callback_query(AdminPlanCallback.filter())
async def admin_plan_action(
    callback: CallbackQuery,
    callback_data: AdminPlanCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    plans_repo = PlansRepository(session)
    action = callback_data.action
    plan_id = callback_data.plan_id

    # 1. Handle Cancel / Back to Plans List
    if action in {"cancel", "list"}:
        await _show_plans(callback, session)
        return

    plan = await plans_repo.get(plan_id)
    if plan is None:
        await _show_plans(callback, session, prefix="❌ این تعرفه یافت نشد یا قبلاً حذف شده است.\n\n")
        return

    # 2. View Plan Details
    if action == "detail":
        await _show_plan_detail(callback, plan, session)
        return

    # 3. Toggle Active / Disabled
    if action == "toggle":
        await plans_repo.set_active(plan.id, not plan.is_active)
        await session.commit()
        refreshed = await plans_repo.get(plan.id)
        await _show_plan_detail(callback, refreshed, session)
        return

    # 4. Show Delete Confirmation Prompt
    if action == "delete":
        await callback.message.edit_text(
            f"⚠️ <b>آیا از حذف تعرفه {escape(plan.title)} مطمئن هستید؟</b>\n\n"
            "در صورت تایید، تمام سفارش‌ها و موجودی‌های متصل به این تعرفه نیز پاکسازی خواهند شد.",
            reply_markup=plan_delete_confirm_keyboard(plan),
            parse_mode="HTML"
        )
        return

    # 5. Execute Safe Cascade Deletion
    if action == "delete_confirm":
        try:
            order_ids_subquery = select(Order.id).where(Order.plan_id == plan.id)

            # Break circular foreign key dependencies safely
            try:
                await session.execute(
                    update(ConfigInventory)
                    .where(ConfigInventory.reserved_by_order_id.in_(order_ids_subquery))
                    .values(reserved_by_order_id=None)
                )
                await session.execute(
                    update(Order)
                    .where(Order.plan_id == plan.id)
                    .values(config_inventory_id=None)
                )
                await session.execute(delete(Payment).where(Payment.order_id.in_(order_ids_subquery)))
                await session.execute(delete(AffiliateCommission).where(AffiliateCommission.order_id.in_(order_ids_subquery)))
                await session.execute(delete(ConfigInventory).where(ConfigInventory.plan_id == plan.id))
            except Exception:
                pass

            # Delete orders, services, and the plan record
            await session.execute(delete(Order).where(Order.plan_id == plan.id))
            await session.execute(delete(VPNService).where(VPNService.plan_id == plan.id))
            await plans_repo.delete(plan.id)
            await session.commit()

            await _show_plans(callback, session, prefix="✅ تعرفه و رکوردهای مرتبط با موفقیت حذف شدند.\n\n")
        except Exception as e:
            await session.rollback()
            await callback.message.answer(f"❌ خطا در حذف تعرفه: {str(e)}")
        return