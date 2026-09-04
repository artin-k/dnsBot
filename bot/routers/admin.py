# bot/routers/admin.py
from __future__ import annotations

import hmac
import hashlib
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo
import jdatetime
import httpx
import structlog

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as TelegramUser, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings, SLOT_CONFIGS
from app.models import User, VPNService, IPAuthToken
from app.repositories.plans import PlansRepository
from app.repositories.reports import ReportsRepository
from app.repositories.users import UsersRepository
from app.services.payment_service import ApprovedPaymentResult
from app.utils.formatting import format_money

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
    setting_edit_keyboard,
    plans_management_keyboard,
    users_admin_keyboard,
    user_detail_keyboard,
)
from bot.keyboards.main_menu import main_menu_keyboard
from bot.states.admin import AdminSettingsStates, AdminSearchStates, AdminAddPlanStates

# Sub-routers
from bot.routers import admin_orders, admin_plans

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
🔹 Primary: <code>{escape(ipv4_primary)}</code>
🔹 Secondary: <code>{escape(ipv4_secondary)}</code>

{agh_section}
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
        "cat_services": ("🛍 سرویس‌ها", admin_services_keyboard()),
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

    if action == "broadcast":
        from bot.states.admin import AdminBroadcastStates
        await state.set_state(AdminBroadcastStates.text)
        await callback.message.edit_text("لطفاً متن پیام همگانی خود را ارسال کنید:")
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