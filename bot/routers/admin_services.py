# bot/routers/admin_services.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from html import escape
from zoneinfo import ZoneInfo
import jdatetime
import structlog

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import Settings, SLOT_CONFIGS
from app.models import VPNService, VPNServiceStatus, User
from app.repositories.users import UsersRepository
from app.services.adguard import AdGuardHomeService
from app.services.controld import ControlDService
from app.utils.formatting import calculate_remaining_time_fa
from bot.keyboards.admin import (
    AdminActionCallback,
    AdminServiceCallback,
    services_admin_keyboard,
    service_detail_keyboard,
    service_change_loc_keyboard,
)

router = Router(name="admin_services")
logger = structlog.get_logger(__name__)


class AdminServiceStates(StatesGroup):
    waiting_extend_days = State()
    waiting_search_query = State()


# ============================================================================
# SEARCH SUBSCRIPTIONS (SMART MULTI-FIELD SEARCH)
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "search"))
async def ask_search_query(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminServiceStates.waiting_search_query)
    await callback.message.answer(
        "🔎 لطفاً <b>یوزرنیم تلگرام</b>، <b>آیدی عددی</b>، یا <b>آی‌پی</b> را ارسال کنید:",
        parse_mode="HTML"
    )


@router.message(AdminServiceStates.waiting_search_query, F.text)
async def process_search_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw_query = (message.text or "").strip()
    await state.clear()

    # 1. Convert Persian/Arabic digits to English digits
    persian_to_eng = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    clean_query = raw_query.translate(persian_to_eng)

    # 2. Strip URLs and @ symbol
    clean_query = clean_query.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "")
    clean_query = clean_query.removeprefix("@").strip()

    if not clean_query:
        await message.answer("❌ عبارت جستجو نمی‌تواند خالی باشد.")
        return

    # 3. Build multi-criteria search
    conditions = [
        User.telegram_username.ilike(f"%{clean_query}%"),
        User.first_name.ilike(f"%{clean_query}%"),
        VPNService.username.ilike(f"%{clean_query}%"),
    ]

    if clean_query.isdigit():
        num_val = int(clean_query)
        conditions.extend([
            User.telegram_id == num_val,
            VPNService.id == num_val,
            VPNService.user_id == num_val,
        ])

    if "." in clean_query:
        conditions.append(VPNService.authorized_ip.ilike(f"%{clean_query}%"))

    # 4. Query subscriptions with joined User
    stmt = (
        select(VPNService)
        .join(User, VPNService.user_id == User.id)
        .options(
            joinedload(VPNService.user),
            joinedload(VPNService.plan),
        )
        .where(or_(*conditions))
        .order_by(VPNService.created_at.desc())
        .limit(15)
    )

    res = await session.execute(stmt)
    services = list(res.scalars().unique().all())

    if not services:
        # Check if the user exists but has no subscriptions
        user_check_stmt = select(User).where(
            or_(
                User.telegram_username.ilike(f"%{clean_query}%"),
                User.first_name.ilike(f"%{clean_query}%"),
                (User.telegram_id == int(clean_query)) if clean_query.isdigit() else False,
            )
        )
        user_res = await session.execute(user_check_stmt)
        matched_user = user_res.scalars().first()

        if matched_user:
            uname = f"@{matched_user.telegram_username}" if matched_user.telegram_username else matched_user.first_name
            await message.answer(
                f"👤 کاربر <b>{escape(uname)}</b> (آیدی: <code>{matched_user.telegram_id}</code>) در سیستم یافت شد، اما <b>هیچ اشتراک DNS ثبت‌شده‌ای ندارد</b>.",
                parse_mode="HTML",
            )
            return

        await message.answer(
            f"❌ هیچ اشتراکی برای «<code>{escape(raw_query)}</code>» یافت نشد.\n\n"
            "می‌توانید با <b>یوزرنیم تلگرام</b> (مثلاً <code>artin</code>)، <b>آیدی عددی</b>، یا <b>آی‌پی</b> جستجو کنید.",
            parse_mode="HTML",
        )
        return

    # 5. Display results using the fixed keyboard
    await message.answer(
        f"🔎 نتایج جستجو برای «<b>{escape(raw_query)}</b>» ({len(services)} اشتراک یافت شد):",
        reply_markup=services_admin_keyboard(services),
        parse_mode="HTML",
    )

async def _is_admin(telegram_id: int | None, session: AsyncSession, settings: Settings) -> bool:
    if telegram_id is None:
        return False
    if settings.root_admin_telegram_id and telegram_id == settings.root_admin_telegram_id:
        return True
    if telegram_id in settings.admin_ids:
        return True
    user = await UsersRepository(session).get_by_telegram_id(telegram_id)
    return bool(user and user.is_admin)


def _format_service_card(service: VPNService) -> str:
    user = service.user
    user_str = f"{escape(user.first_name or 'کاربر')} | @{escape(user.telegram_username or 'ندارد')} (ID: <code>{user.telegram_id}</code>)" if user else "نامشخص"

    raw_username = service.username or ""
    clean_name = raw_username.split("|")[0].strip()

    country_display = "پیش‌فرض"
    for cfg in SLOT_CONFIGS.values():
        if cfg["device_id"] == service.controld_device_id:
            country_display = cfg["name"]
            break

    now = datetime.now(timezone.utc)
    exp = service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at and service.expire_at.tzinfo is None else service.expire_at

    if service.status == "disabled":
        status_fa = "🔴 غیرفعال (قطع توسط ادمین)"
    elif exp and exp <= now:
        status_fa = "🟡 منقضی شده"
    else:
        status_fa = "🟢 فعال"

    tehran_tz = ZoneInfo("Asia/Tehran")
    try:
        shamsi_expire = jdatetime.datetime.fromgregorian(
            datetime=exp.astimezone(tehran_tz).replace(tzinfo=None)
        ).strftime("%Y/%m/%d - %H:%M")
    except Exception:
        shamsi_expire = exp.strftime("%Y-%m-%d %H:%M") if exp else "-"

    duration_text = calculate_remaining_time_fa(exp)

    return f"""🛍 <b>مدیریت اشتراک DNS (شناسه: {service.id})</b>

👤 <b>کاربر:</b> {user_str}
📱 <b>نام دستگاه:</b> <code>{escape(clean_name)}</code>
📌 <b>وضعیت:</b> {status_fa}
🗺 <b>سرور (لوکیشن):</b> {escape(country_display)}
🌐 <b>آی‌پی فعال:</b> <code>{escape(service.authorized_ip or 'ثبت نشده ❌')}</code>
⚡ <b>تعرفه:</b> {escape(service.plan.title if service.plan else ('اکانت تست' if service.is_test_account else 'نامشخص'))}
⏳ <b>زمان باقی‌مانده:</b> {duration_text}
🗓 <b>تاریخ انقضاء:</b> <code>{escape(shamsi_expire)}</code>"""


# ============================================================================
# LIST & NAVIGATION
# ============================================================================

@router.callback_query(AdminActionCallback.filter(F.action == "services"))
@router.callback_query(AdminServiceCallback.filter(F.action == "list"))
async def list_services(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    callback_data: AdminServiceCallback | None = None,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    await callback.answer()
    page = callback_data.page if isinstance(callback_data, AdminServiceCallback) else 0
    limit = 8
    offset = page * limit

    stmt = (
        select(VPNService)
        .options(joinedload(VPNService.user), joinedload(VPNService.plan))
        .order_by(VPNService.created_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    res = await session.execute(stmt)
    services = list(res.scalars().unique().all())
    has_next = len(services) > limit
    if has_next:
        services = services[:limit]

    if not services and page == 0:
        await callback.message.edit_text("هیچ اشتراکی در دیتابیس ثبت نشده است.")
        return

    text = f"🛍 <b>لیست اشتراک‌های DNS (صفحه {page + 1}):</b>\n\nبرای مدیریت هر اشتراک، روی دکمه آن کلیک کنید:"
    await callback.message.edit_text(
        text,
        reply_markup=services_admin_keyboard(services, page=page, has_next=has_next),
        parse_mode="HTML",
    )


@router.callback_query(AdminServiceCallback.filter(F.action == "detail"))
async def service_detail(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    stmt = (
        select(VPNService)
        .options(joinedload(VPNService.user), joinedload(VPNService.plan))
        .where(VPNService.id == callback_data.service_id)
        .limit(1)
    )
    res = await session.execute(stmt)
    service = res.scalars().first()

    if not service:
        await callback.answer("اشتراک پیدا نشد.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(
        _format_service_card(service),
        reply_markup=service_detail_keyboard(service, page=callback_data.page),
        parse_mode="HTML",
    )


# ============================================================================
# ACTIVATE & DISABLE
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "disable"))
async def disable_service(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    service = await session.get(VPNService, callback_data.service_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return

    service.status = "disabled"

    # Deauthorize IP on Control D and AdGuard
    if service.authorized_ip:
        if service.controld_device_id:
            try:
                await ControlDService(settings).deauthorize_ip(service.controld_device_id, service.authorized_ip)
            except Exception:
                logger.exception("admin_disable_controld_failed", service_id=service.id)
        adg = AdGuardHomeService(settings)
        if adg.is_configured():
            try:
                await adg.deauthorize_client_ip(service.authorized_ip)
            except Exception:
                logger.exception("admin_disable_adguard_failed", service_id=service.id)

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("admin_disable_database_commit_failed", service_id=service.id)
        await callback.answer("❌ ذخیره تغییرات ناموفق بود.", show_alert=True)
        return
    await callback.answer("🔴 اشتراک غیرفعال شد و دسترسی DNS قطع گردید.", show_alert=True)

    # Refresh card
    refreshed = (await session.execute(
        select(VPNService).options(joinedload(VPNService.user), joinedload(VPNService.plan)).where(VPNService.id == service.id)
    )).scalars().first()
    await callback.message.edit_text(
        _format_service_card(refreshed),
        reply_markup=service_detail_keyboard(refreshed, page=callback_data.page),
        parse_mode="HTML",
    )


@router.callback_query(AdminServiceCallback.filter(F.action == "activate"))
async def activate_service(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ عدم دسترسی.", show_alert=True)
        return

    service = await session.get(VPNService, callback_data.service_id)
    if not service:
        await callback.answer("سرویس پیدا نشد.", show_alert=True)
        return

    service.status = "active"

    # If already expired, grant 30 days
    now = datetime.now(timezone.utc)
    if not service.expire_at or service.expire_at <= now:
        service.expire_at = now + timedelta(days=30)

    # Re-authorize IP if user had an IP registered
    if service.authorized_ip:
        if service.controld_device_id:
            try:
                await ControlDService(settings).authorize_ip(service.controld_device_id, service.authorized_ip)
            except Exception:
                logger.exception("admin_activate_controld_failed", service_id=service.id)
        adg = AdGuardHomeService(settings)
        if adg.is_configured():
            try:
                await adg.allow_client_ip(service.authorized_ip)
            except Exception:
                logger.exception("admin_activate_adguard_failed", service_id=service.id)

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("admin_activate_database_commit_failed", service_id=service.id)
        await callback.answer("❌ ذخیره تغییرات ناموفق بود.", show_alert=True)
        return
    await callback.answer("🟢 اشتراک فعال شد.", show_alert=True)

    refreshed = (await session.execute(
        select(VPNService).options(joinedload(VPNService.user), joinedload(VPNService.plan)).where(VPNService.id == service.id)
    )).scalars().first()
    await callback.message.edit_text(
        _format_service_card(refreshed),
        reply_markup=service_detail_keyboard(refreshed, page=callback_data.page),
        parse_mode="HTML",
    )


# ============================================================================
# EXTEND DAYS (MANUAL)
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "extend"))
async def ask_extend_days(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.set_state(AdminServiceStates.waiting_extend_days)
    await state.update_data(service_id=callback_data.service_id, page=callback_data.page)
    await callback.message.answer(
        "🗓 <b>تمدید دستی اشتراک:</b>\n\nلطفاً تعداد روزهایی که می‌خواهید به اشتراک اضافه شود را به صورت عدد انگلیسی ارسال کنید (مثلاً <code>30</code>):",
        parse_mode="HTML",
    )


@router.message(AdminServiceStates.waiting_extend_days, F.text)
async def process_extend_days(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    digits = (message.text or "").strip()
    if not digits.isdigit() or int(digits) <= 0:
        await message.answer("❌ لطفاً یک عدد صحیح و مثبت وارد کنید (مثال: 30):")
        return

    days_to_add = int(digits)
    data = await state.get_data()
    service_id = data.get("service_id")
    page = data.get("page", 0)

    service = await session.get(VPNService, service_id)
    if not service:
        await state.clear()
        await message.answer("سرویس پیدا نشد.")
        return

    now = datetime.now(timezone.utc)
    base_time = service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at and service.expire_at > now else now
    service.expire_at = base_time + timedelta(days=days_to_add)
    service.status = "active"
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("admin_extend_database_commit_failed", service_id=service.id)
        await state.clear()
        await message.answer("❌ ذخیره تمدید اشتراک ناموفق بود. لطفاً دوباره تلاش کنید.")
        return
    await state.clear()

    await message.answer(f"✅ با موفقیت <b>{days_to_add} روز</b> به اشتراک اضافه شد.", parse_mode="HTML")

    refreshed = (await session.execute(
        select(VPNService).options(joinedload(VPNService.user), joinedload(VPNService.plan)).where(VPNService.id == service.id)
    )).scalars().first()
    await message.answer(
        _format_service_card(refreshed),
        reply_markup=service_detail_keyboard(refreshed, page=page),
        parse_mode="HTML",
    )


# ============================================================================
# CLEAR IP (RESET)
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "clear_ip"))
async def clear_service_ip(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    service = await session.get(VPNService, callback_data.service_id)
    if not service or not service.authorized_ip:
        await callback.answer("آی‌پی برای حذف وجود ندارد.", show_alert=True)
        return

    old_ip = service.authorized_ip
    if service.controld_device_id:
        try:
            await ControlDService(settings).deauthorize_ip(service.controld_device_id, old_ip)
        except Exception:
            logger.exception("admin_clear_ip_controld_failed", service_id=service.id)

    adg = AdGuardHomeService(settings)
    if adg.is_configured():
        try:
            await adg.deauthorize_client_ip(old_ip)
        except Exception:
            logger.exception("admin_clear_ip_adguard_failed", service_id=service.id)

    service.authorized_ip = None
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("admin_clear_ip_database_commit_failed", service_id=service.id)
        await callback.answer("❌ ذخیره حذف IP ناموفق بود.", show_alert=True)
        return
    await callback.answer("🧹 آی‌پی کاربر ریست و از سرورها حذف شد.", show_alert=True)

    refreshed = (await session.execute(
        select(VPNService).options(joinedload(VPNService.user), joinedload(VPNService.plan)).where(VPNService.id == service.id)
    )).scalars().first()
    await callback.message.edit_text(
        _format_service_card(refreshed),
        reply_markup=service_detail_keyboard(refreshed, page=callback_data.page),
        parse_mode="HTML",
    )


# ============================================================================
# CHANGE LOCATION
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "change_loc_menu"))
async def change_loc_menu(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "🗺 لطفاً سرور (لوکیشن) جدید را برای این اشتراک انتخاب کنید:",
        reply_markup=service_change_loc_keyboard(callback_data.service_id, page=callback_data.page),
    )


@router.callback_query(AdminServiceCallback.filter(F.action == "apply_loc"))
async def apply_loc_change(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    service = await session.get(VPNService, callback_data.service_id)
    slot_num = callback_data.slot_num
    if not service or slot_num not in SLOT_CONFIGS:
        await callback.answer("اطلاعات نامعتبر است.", show_alert=True)
        return

    new_cfg = SLOT_CONFIGS[slot_num]
    old_device = service.controld_device_id
    new_device = new_cfg["device_id"]

    # Migrate IP if one was registered
    if service.authorized_ip and old_device != new_device:
        cd = ControlDService(settings)
        try:
            await cd.deauthorize_ip(old_device, service.authorized_ip)
            await cd.authorize_ip(new_device, service.authorized_ip)
        except Exception:
            logger.exception("admin_change_location_controld_failed", service_id=service.id)

    clean_name = (service.username or "دستگاه").split("|")[0].strip()
    service.username = f"{clean_name}|default|{slot_num}"
    service.controld_device_id = new_device
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("admin_change_location_database_commit_failed", service_id=service.id)
        await callback.answer("❌ ذخیره تغییر سرور ناموفق بود.", show_alert=True)
        return

    await callback.answer(f"✅ سرور به {new_cfg['name']} تغییر یافت.", show_alert=True)

    refreshed = (await session.execute(
        select(VPNService).options(joinedload(VPNService.user), joinedload(VPNService.plan)).where(VPNService.id == service.id)
    )).scalars().first()
    await callback.message.edit_text(
        _format_service_card(refreshed),
        reply_markup=service_detail_keyboard(refreshed, page=callback_data.page),
        parse_mode="HTML",
    )


# ============================================================================
# DELETE SUBSCRIPTION
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "delete_confirm"))
async def confirm_delete_service(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
) -> None:
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="⚠️ بله، کاملاً حذف شود", callback_data=AdminServiceCallback(action="delete_execute", service_id=callback_data.service_id, page=callback_data.page))
    builder.button(text="❌ لغو", callback_data=AdminServiceCallback(action="detail", service_id=callback_data.service_id, page=callback_data.page))
    builder.adjust(1)
    await callback.message.edit_text(
        f"⚠️ <b>آیا از حذف کامل این اشتراک مطمئن هستید؟</b>\nدسترسی کاربر قطع شده و رکورد آن از دیتابیس پاک خواهد شد.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(AdminServiceCallback.filter(F.action == "delete_execute"))
async def execute_delete_service(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    service = await session.get(VPNService, callback_data.service_id)
    if service:
        if service.authorized_ip and service.controld_device_id:
            try:
                await ControlDService(settings).deauthorize_ip(service.controld_device_id, service.authorized_ip)
            except Exception:
                logger.exception("admin_delete_controld_failed", service_id=service.id)
        await session.delete(service)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("admin_delete_database_commit_failed", service_id=callback_data.service_id)
            await callback.answer("❌ حذف اشتراک ناموفق بود.", show_alert=True)
            return

    await callback.answer("🗑 اشتراک با موفقیت حذف شد.", show_alert=True)
    # Return to page 0
    await list_services(callback, session, settings)


# ============================================================================
# SEARCH SUBSCRIPTIONS
# ============================================================================

@router.callback_query(AdminServiceCallback.filter(F.action == "search"))
async def ask_search_query(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminServiceStates.waiting_search_query)
    await callback.message.answer("🔎 لطفاً <b>آیدی عددی کاربر</b>، <b>نام کاربری تلگرام</b>، یا <b>آی‌پی</b> مورد نظر را ارسال کنید:", parse_mode="HTML")


@router.message(AdminServiceStates.waiting_search_query, F.text)
async def process_search_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    await state.clear()

    stmt = (
        select(VPNService)
        .options(joinedload(VPNService.user), joinedload(VPNService.plan))
        .join(VPNService.user)
    )

    if query.isdigit():
        stmt = stmt.where(or_(User.telegram_id == int(query), VPNService.id == int(query)))
    elif "." in query:
        stmt = stmt.where(VPNService.authorized_ip == query)
    else:
        clean_user = query.removeprefix("@")
        stmt = stmt.where(or_(User.telegram_username.ilike(f"%{clean_user}%"), VPNService.username.ilike(f"%{clean_user}%")))

    res = await session.execute(stmt.limit(10))
    services = list(res.scalars().unique().all())

    if not services:
        await message.answer("❌ هیچ اشتراکی با این مشخصات یافت نشد.")
        return

    builder = InlineKeyboardBuilder()
    for s in services:
        clean_name = (s.username or "دستگاه").split("|")[0].strip()
        builder.button(
            text=f"🛍 {clean_name} (ID: {s.id})",
            callback_data=AdminServiceCallback(action="detail", service_id=s.id, page=0),
        )
    builder.button(text="↩️ بازگشت به لیست", callback_data=AdminServiceCallback(action="list", page=0))
    builder.adjust(1)

    await message.answer(f"🔍 <b>نتایج جستجو برای '{escape(query)}':</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
