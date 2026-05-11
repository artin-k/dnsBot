from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderKind, Payment, User, VPNServiceStatus, WalletTransactionStatus, WalletTransactionType
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.test_accounts import TestAccountsRepository
from app.repositories.users import UsersRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.services.order_status import order_kind_label
from app.services.payment_service import (
    ApprovedPaymentResult,
    PaymentAlreadyProcessedError,
    PaymentApprovalError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.wallet_service import WalletService, WalletTopupAlreadyProcessedError, WalletTopupError
from app.services.vpn_panel import VPNPanelService
from app.utils.formatting import (
    format_datetime,
    format_money,
    format_service_status_fa,
    format_wallet_transaction_status_fa,
)
from bot import texts
from bot.keyboards.admin import (
    AdminActionCallback,
    AdminPaymentCallback,
    AdminPlanCallback,
    AdminServiceCallback,
    AdminTestAccountCallback,
    AdminUserCallback,
    add_plan_confirm_keyboard,
    add_test_account_confirm_keyboard,
    admin_main_keyboard,
    broadcast_confirm_keyboard,
    pending_payments_keyboard,
    plan_detail_keyboard,
    plans_management_keyboard,
    service_detail_keyboard,
    services_admin_keyboard,
    test_account_detail_keyboard,
    test_accounts_keyboard,
    user_detail_keyboard,
    users_admin_keyboard,
    wallet_topups_keyboard,
)
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.wallet import WalletTopupReviewCallback
from bot.states.admin import (
    AdminAddPlanStates,
    AdminAddTestAccountStates,
    AdminBroadcastStates,
    AdminEditPlanStates,
    AdminEditTestAccountStates,
    AdminSearchStates,
    AdminServiceEditStates,
    AdminWalletAdjustStates,
)

router = Router(name="admin")
logger = structlog.get_logger(__name__)

EDIT_FIELD_MAP = {
    "edit_title": ("title", "عنوان جدید تعرفه را ارسال کنید:", "title"),
    "edit_desc": ("description", "توضیحات جدید را ارسال کنید. برای خالی کردن، - بفرستید:", "description"),
    "edit_duration": ("duration_days", "مدت جدید را به روز ارسال کنید:", "positive_int"),
    "edit_volume": ("volume_gb", "حجم جدید را به گیگ ارسال کنید:", "positive_int"),
    "edit_price": ("price", "قیمت جدید را به تومان ارسال کنید:", "positive_int"),
    "edit_sort": ("sort_order", "ترتیب نمایش جدید را ارسال کنید. مقدار 0 مجاز است:", "int"),
}


@router.message(Command("admin"))
async def admin_panel(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        await message.answer("⛔ شما دسترسی مدیریت ندارید.")
        return
    await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())


@router.callback_query(AdminActionCallback.filter())
async def admin_action(
    callback: CallbackQuery,
    callback_data: AdminActionCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    action = callback_data.action
    await callback.answer()

    if action in {"panel", "back"}:
        await state.clear()
        if action == "back":
            if callback.message:
                await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())
        elif callback.message:
            await callback.message.edit_text(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        return

    if action == "payments":
        await state.clear()
        await _show_pending_payments(callback, session)
        return

    if action == "wallet_topups":
        await state.clear()
        await _show_pending_wallet_topups(callback, session)
        return

    if action == "plans":
        await state.clear()
        await _show_plans(callback, session)
        return

    if action == "test_accounts":
        await state.clear()
        await _show_test_accounts(callback, session)
        return

    if action == "users":
        await state.clear()
        await _show_users(callback, session)
        return

    if action == "services":
        await state.clear()
        await _show_services(callback, session)
        return

    if action == "orders":
        await state.clear()
        await _show_recent_orders(callback, session)
        return

    if action == "dice":
        await state.clear()
        await _show_dice(callback, session, settings)
        return

    if action == "settings":
        await state.clear()
        await _show_settings(callback, settings)
        return

    if action == "broadcast":
        await state.clear()
        await state.set_state(AdminBroadcastStates.text)
        if callback.message:
            await callback.message.answer("متن پیام همگانی را ارسال کنید.")
        return

    if action == "add_plan":
        await state.clear()
        await state.set_state(AdminAddPlanStates.title)
        if callback.message:
            await callback.message.answer("عنوان تعرفه را ارسال کنید.")
        return

    if action == "save_add_plan":
        await _save_add_plan(callback, state, session)
        return

    if action == "cancel_add_plan":
        await state.clear()
        if callback.message:
            await callback.message.answer("افزودن تعرفه لغو شد.", reply_markup=admin_main_keyboard())
        return

    if action == "save_test_account":
        await _save_test_account(callback, state, session)
        return

    if action == "cancel_test_account":
        await state.clear()
        if callback.message:
            await callback.message.answer("افزودن اکانت تست لغو شد.", reply_markup=admin_main_keyboard())
        return

    if action == "send_broadcast":
        await _send_broadcast(callback, state, session)
        return

    if action == "cancel_broadcast":
        await state.clear()
        if callback.message:
            await callback.message.answer("ارسال پیام همگانی لغو شد.", reply_markup=admin_main_keyboard())
        return

    if callback.message:
        await callback.message.answer(texts.COMING_SOON_TEXT)


@router.callback_query(AdminPaymentCallback.filter())
async def admin_payment_action(
    callback: CallbackQuery,
    callback_data: AdminPaymentCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    payment_service = PaymentService(session, VPNPanelService(), settings)
    try:
        if callback_data.action == "approve":
            result = await payment_service.approve_payment(callback_data.payment_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text=_approved_message(result),
            )
            await callback.answer("پرداخت تایید شد.")
            await _remove_admin_buttons(callback)
        elif callback_data.action == "reject":
            result = await payment_service.reject_payment(callback_data.payment_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text="""❌ پرداخت شما توسط مدیریت تایید نشد.
در صورت وجود مشکل با پشتیبانی در ارتباط باشید.""",
            )
            await callback.answer("پرداخت رد شد.")
            await _remove_admin_buttons(callback)
    except PaymentExpiredError:
        await callback.answer(texts.EXPIRED_ORDER_TEXT, show_alert=True)
    except PaymentAlreadyProcessedError:
        await callback.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
    except PaymentApprovalError:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)


@router.callback_query(WalletTopupReviewCallback.filter())
async def admin_wallet_topup_action(
    callback: CallbackQuery,
    callback_data: WalletTopupReviewCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    try:
        if callback_data.action == "approve":
            result = await WalletService(session).approve_topup(callback_data.transaction_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text=f"""✅ شارژ کیف پول شما تایید شد.

💵 مبلغ شارژ: {format_money(result.amount)} تومان
🏦 موجودی جدید: {format_money(result.wallet_balance)} تومان""",
            )
            await callback.answer("شارژ کیف پول تایید شد.")
            await _remove_admin_buttons(callback)
            return

        if callback_data.action == "reject":
            result = await WalletService(session).reject_topup(callback_data.transaction_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text="❌ رسید شارژ کیف پول شما تایید نشد. در صورت وجود مشکل با پشتیبانی در ارتباط باشید.",
            )
            await callback.answer("شارژ کیف پول رد شد.")
            await _remove_admin_buttons(callback)
            return

        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
    except WalletTopupAlreadyProcessedError:
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
    except WalletTopupError:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)


@router.callback_query(AdminPlanCallback.filter())
async def admin_plan_action(
    callback: CallbackQuery,
    callback_data: AdminPlanCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await callback.answer()
    plans_repo = PlansRepository(session)
    plan = await plans_repo.get(callback_data.plan_id)
    if plan is None:
        await _safe_edit_or_answer(callback, "تعرفه پیدا نشد.")
        return

    action = callback_data.action
    if action == "detail":
        await _show_plan_detail(callback, plan)
        return

    if action in EDIT_FIELD_MAP:
        field, prompt, validator = EDIT_FIELD_MAP[action]
        await state.set_state(AdminEditPlanStates.value)
        await state.update_data(plan_id=plan.id, field=field, validator=validator)
        if callback.message:
            await callback.message.answer(prompt)
        return

    if action == "toggle":
        await plans_repo.set_active(plan.id, not plan.is_active)
        await session.commit()
        refreshed = await plans_repo.get(plan.id)
        await _show_plan_detail(callback, refreshed)
        return

    if action == "delete":
        if await plans_repo.has_usage(plan.id):
            await plans_repo.set_active(plan.id, False)
            await session.commit()
            refreshed = await plans_repo.get(plan.id)
            detail = _format_plan_detail(refreshed) if refreshed is not None else ""
            await _safe_edit_or_answer(
                callback,
                f"این تعرفه سفارش یا سرویس ثبت‌شده دارد؛ حذف نشد و به‌جای آن غیرفعال شد.\n\n{detail}",
                reply_markup=plan_detail_keyboard(refreshed) if refreshed is not None else None,
            )
            return
        await plans_repo.delete(plan.id)
        await session.commit()
        await _show_plans(callback, session, prefix="✅ تعرفه حذف شد.\n\n")
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.")


@router.callback_query(AdminTestAccountCallback.filter())
async def admin_test_account_action(
    callback: CallbackQuery,
    callback_data: AdminTestAccountCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()

    repo = TestAccountsRepository(session)
    action = callback_data.action
    if action == "add":
        await state.clear()
        await state.set_state(AdminAddTestAccountStates.title)
        if callback.message:
            await callback.message.answer("عنوان اکانت تست را ارسال کنید.")
        return

    account = await repo.get(callback_data.test_account_id)
    if account is None:
        await _safe_edit_or_answer(callback, "اکانت تست پیدا نشد.")
        return

    if action == "detail":
        await _safe_edit_or_answer(callback, _format_test_account_detail(account), reply_markup=test_account_detail_keyboard(account))
        return
    if action in {"edit_title", "edit_desc", "edit_config", "edit_sub", "edit_duration", "edit_max"}:
        await state.set_state(AdminEditTestAccountStates.value)
        await state.update_data(test_account_id=account.id, field=action)
        prompts = {
            "edit_title": "عنوان جدید را ارسال کنید.",
            "edit_desc": "توضیحات جدید را ارسال کنید. برای خالی کردن، - بفرستید.",
            "edit_config": "لینک کانفیگ جدید را ارسال کنید.",
            "edit_sub": "لینک اشتراک جدید را ارسال کنید. برای خالی کردن، - بفرستید.",
            "edit_duration": "مدت تست جدید را به ساعت ارسال کنید.",
            "edit_max": "حداکثر دریافت جدید را ارسال کنید. 0 یعنی نامحدود.",
        }
        if callback.message:
            await callback.message.answer(prompts[action])
        return
    if action == "toggle":
        account.is_active = not account.is_active
        await session.commit()
        await _safe_edit_or_answer(callback, _format_test_account_detail(account), reply_markup=test_account_detail_keyboard(account))
        return
    if action == "delete":
        if await repo.has_claims(account.id):
            account.is_active = False
            await session.commit()
            await _safe_edit_or_answer(callback, "این اکانت تست دارای دریافت‌کننده است و حذف نمی‌شود. به جای حذف، غیرفعال شد.")
            return
        await session.delete(account)
        await session.commit()
        await _show_test_accounts(callback, session, prefix="✅ اکانت تست حذف شد.\n\n")
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.")


@router.callback_query(AdminUserCallback.filter())
async def admin_user_action(
    callback: CallbackQuery,
    callback_data: AdminUserCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()

    if callback_data.action == "search":
        await state.set_state(AdminSearchStates.user_query)
        if callback.message:
            await callback.message.answer("آیدی عددی، یوزرنیم یا شماره موبایل کاربر را ارسال کنید.")
        return

    user = await session.get(User, callback_data.user_id)
    if user is None:
        await _safe_edit_or_answer(callback, "کاربر پیدا نشد.")
        return

    if callback_data.action == "detail":
        await _show_user_detail(callback, session, user)
        return
    if callback_data.action in {"add_wallet", "sub_wallet"}:
        await state.set_state(AdminWalletAdjustStates.amount)
        await state.update_data(user_id=user.id, direction="add" if callback_data.action == "add_wallet" else "sub")
        if callback.message:
            await callback.message.answer("مبلغ تغییر موجودی را به تومان ارسال کنید.")
        return
    if callback_data.action == "toggle_admin":
        if callback.from_user and user.telegram_id == callback.from_user.id:
            await callback.answer("برای جلوگیری از حذف دسترسی خودتان، این عملیات انجام نشد.", show_alert=True)
            return
        user.is_admin = not user.is_admin
        await session.commit()
        await _show_user_detail(callback, session, user)
        return
    if callback_data.action == "orders":
        await _show_user_orders(callback, session, user)
        return
    if callback_data.action == "services":
        await _show_user_services(callback, session, user)
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.")


@router.callback_query(AdminServiceCallback.filter())
async def admin_service_action(
    callback: CallbackQuery,
    callback_data: AdminServiceCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()

    if callback_data.action == "search":
        await state.set_state(AdminSearchStates.service_query)
        if callback.message:
            await callback.message.answer("نام کاربری سرویس یا آیدی عددی کاربر را ارسال کنید.")
        return

    service = await ServicesRepository(session).get(callback_data.service_id)
    if service is None:
        await _safe_edit_or_answer(callback, "سرویس پیدا نشد.")
        return

    if callback_data.action == "detail":
        await _show_service_detail(callback, service)
        return
    if callback_data.action == "activate":
        service.status = VPNServiceStatus.ACTIVE.value
        await session.commit()
        await _show_service_detail(callback, service)
        return
    if callback_data.action == "disable":
        service.status = VPNServiceStatus.DISABLED.value
        await session.commit()
        await _show_service_detail(callback, service)
        return
    if callback_data.action in {"extend", "edit_config", "edit_sub"}:
        await state.set_state(AdminServiceEditStates.value)
        await state.update_data(service_id=service.id, action=callback_data.action)
        prompt = {
            "extend": "تعداد روز تمدید دستی را ارسال کنید.",
            "edit_config": "لینک کانفیگ جدید را ارسال کنید. برای خالی کردن، - بفرستید.",
            "edit_sub": "لینک اشتراک جدید را ارسال کنید. برای خالی کردن، - بفرستید.",
        }[callback_data.action]
        if callback.message:
            await callback.message.answer(prompt)
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.")
@router.message(AdminAddPlanStates.title)
async def add_plan_title(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("عنوان نمی‌تواند خالی باشد. دوباره ارسال کنید.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminAddPlanStates.description)
    await message.answer("توضیحات تعرفه را ارسال کنید. برای توضیحات خالی، - بفرستید.")


@router.message(AdminAddPlanStates.description)
async def add_plan_description(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    description = (message.text or "").strip()
    await state.update_data(description=None if description == "-" else description)
    await state.set_state(AdminAddPlanStates.duration_days)
    await message.answer("مدت اعتبار تعرفه را به روز ارسال کنید. مثال: 30")


@router.message(AdminAddPlanStates.duration_days)
async def add_plan_duration(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_positive_int(message.text)
    if value is None:
        await message.answer("لطفاً یک عدد صحیح مثبت ارسال کنید.")
        return
    await state.update_data(duration_days=value)
    await state.set_state(AdminAddPlanStates.volume_gb)
    await message.answer("حجم تعرفه را به گیگ ارسال کنید. مثال: 10")


@router.message(AdminAddPlanStates.volume_gb)
async def add_plan_volume(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_positive_int(message.text)
    if value is None:
        await message.answer("لطفاً یک عدد صحیح مثبت ارسال کنید.")
        return
    await state.update_data(volume_gb=value)
    await state.set_state(AdminAddPlanStates.price)
    await message.answer("قیمت تعرفه را به تومان ارسال کنید. مثال: 2100000")


@router.message(AdminAddPlanStates.price)
async def add_plan_price(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_positive_int(message.text)
    if value is None:
        await message.answer("لطفاً یک عدد صحیح مثبت ارسال کنید.")
        return
    await state.update_data(price=value)
    await state.set_state(AdminAddPlanStates.sort_order)
    await message.answer("ترتیب نمایش را ارسال کنید. مقدار 0 هم مجاز است.")


@router.message(AdminAddPlanStates.sort_order)
async def add_plan_sort_order(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_int(message.text)
    if value is None:
        await message.answer("لطفاً یک عدد صحیح ارسال کنید.")
        return
    await state.update_data(sort_order=value)
    await state.set_state(AdminAddPlanStates.confirm)
    data = await state.get_data()
    await message.answer(_format_plan_data_summary(data), reply_markup=add_plan_confirm_keyboard())


@router.message(AdminEditPlanStates.value)
async def edit_plan_value(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    plan_id = data.get("plan_id")
    field = data.get("field")
    validator = data.get("validator")
    if not plan_id or not field:
        await state.clear()
        await message.answer("ویرایش قابل ادامه نیست. دوباره تلاش کنید.", reply_markup=admin_main_keyboard())
        return

    parsed = _validate_edit_value(message.text, validator)
    if parsed is _INVALID:
        await message.answer(_validation_error(validator))
        return

    plan = await PlansRepository(session).update_fields(int(plan_id), **{field: parsed})
    await session.commit()
    await state.clear()
    if plan is None:
        await message.answer("تعرفه پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    await message.answer("✅ تعرفه به‌روزرسانی شد.")
    await message.answer(_format_plan_detail(plan), reply_markup=plan_detail_keyboard(plan))


@router.message(AdminAddTestAccountStates.title)
async def add_test_title(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("عنوان نمی‌تواند خالی باشد.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminAddTestAccountStates.description)
    await message.answer("توضیحات را ارسال کنید. برای توضیحات خالی، - بفرستید.")


@router.message(AdminAddTestAccountStates.description)
async def add_test_description(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    text = (message.text or "").strip()
    await state.update_data(description=None if text == "-" else text)
    await state.set_state(AdminAddTestAccountStates.config_link)
    await message.answer("لینک کانفیگ اکانت تست را ارسال کنید.")


@router.message(AdminAddTestAccountStates.config_link)
async def add_test_config(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("لینک کانفیگ نمی‌تواند خالی باشد.")
        return
    await state.update_data(config_link=value)
    await state.set_state(AdminAddTestAccountStates.subscription_link)
    await message.answer("لینک اشتراک را ارسال کنید. برای خالی بودن، - بفرستید.")


@router.message(AdminAddTestAccountStates.subscription_link)
async def add_test_subscription(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = (message.text or "").strip()
    await state.update_data(subscription_link=None if value == "-" else value)
    await state.set_state(AdminAddTestAccountStates.duration_hours)
    await message.answer("مدت تست را به ساعت ارسال کنید. مثال: 24")


@router.message(AdminAddTestAccountStates.duration_hours)
async def add_test_duration(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_positive_int(message.text)
    if value is None:
        await message.answer("لطفاً یک عدد صحیح مثبت ارسال کنید.")
        return
    await state.update_data(duration_hours=value)
    await state.set_state(AdminAddTestAccountStates.max_claims)
    await message.answer("حداکثر تعداد دریافت را ارسال کنید. 0 یعنی نامحدود.")


@router.message(AdminAddTestAccountStates.max_claims)
async def add_test_max_claims(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    value = _parse_int(message.text)
    if value is None or value < 0:
        await message.answer("لطفاً عدد صحیح 0 یا بزرگ‌تر ارسال کنید.")
        return
    await state.update_data(max_claims=value)
    data = await state.get_data()
    await state.set_state(AdminAddTestAccountStates.confirm)
    await message.answer(_format_test_account_data_summary(data), reply_markup=add_test_account_confirm_keyboard())


@router.message(AdminSearchStates.user_query)
async def admin_user_search(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    users = await UsersRepository(session).search(message.text or "")
    await state.clear()
    if not users:
        await message.answer("کاربری با این مشخصات پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    await message.answer("نتایج جستجوی کاربران:", reply_markup=users_admin_keyboard(users))


@router.message(AdminEditTestAccountStates.value)
async def edit_test_account_value(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    account = await TestAccountsRepository(session).get(int(data.get("test_account_id") or 0))
    if account is None:
        await state.clear()
        await message.answer("اکانت تست پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    field = data.get("field")
    value = (message.text or "").strip()
    if field == "edit_title":
        if not value:
            await message.answer("عنوان نمی‌تواند خالی باشد.")
            return
        account.title = value
    elif field == "edit_desc":
        account.description = None if value == "-" else value
    elif field == "edit_config":
        if not value:
            await message.answer("لینک کانفیگ نمی‌تواند خالی باشد.")
            return
        account.config_link = value
    elif field == "edit_sub":
        account.subscription_link = None if value == "-" else value
    elif field == "edit_duration":
        parsed = _parse_positive_int(value)
        if parsed is None:
            await message.answer("لطفاً عدد صحیح مثبت ارسال کنید.")
            return
        account.duration_hours = parsed
    elif field == "edit_max":
        parsed = _parse_int(value)
        if parsed is None or parsed < 0:
            await message.answer("لطفاً عدد صحیح 0 یا بزرگ‌تر ارسال کنید.")
            return
        account.max_claims = parsed
    await session.commit()
    await state.clear()
    await message.answer("✅ اکانت تست به‌روزرسانی شد.")
    await message.answer(_format_test_account_detail(account), reply_markup=test_account_detail_keyboard(account))


@router.message(AdminSearchStates.service_query)
async def admin_service_search(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    services = await ServicesRepository(session).search(message.text or "")
    await state.clear()
    if not services:
        await message.answer("سرویسی با این مشخصات پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    await message.answer("نتایج جستجوی سرویس‌ها:", reply_markup=services_admin_keyboard(services))


@router.message(AdminWalletAdjustStates.amount)
async def admin_wallet_adjust(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    amount = _parse_positive_int(message.text)
    if amount is None:
        await message.answer("لطفاً یک عدد صحیح مثبت ارسال کنید.")
        return
    data = await state.get_data()
    user = await session.get(User, int(data.get("user_id") or 0))
    if user is None:
        await state.clear()
        await message.answer("کاربر پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    signed_amount = amount if data.get("direction") == "add" else -amount
    user.wallet_balance += signed_amount
    await WalletTransactionsRepository(session).create(
        user_id=user.id,
        amount=signed_amount,
        type=WalletTransactionType.ADMIN_ADJUSTMENT.value,
        status=WalletTransactionStatus.APPROVED.value,
        description="تنظیم دستی موجودی توسط مدیریت",
        approved_at=datetime.now(timezone.utc),
    )
    await session.commit()
    await state.clear()
    await message.answer(f"✅ موجودی کاربر به‌روزرسانی شد.\nموجودی جدید: {format_money(user.wallet_balance)} تومان")


@router.message(AdminServiceEditStates.value)
async def admin_service_edit(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    service = await ServicesRepository(session).get(int(data.get("service_id") or 0))
    if service is None:
        await state.clear()
        await message.answer("سرویس پیدا نشد.", reply_markup=admin_main_keyboard())
        return
    action = data.get("action")
    text = (message.text or "").strip()
    if action == "extend":
        days = _parse_positive_int(text)
        if days is None:
            await message.answer("لطفاً تعداد روز را به صورت عدد صحیح مثبت ارسال کنید.")
            return
        service.expire_at = service.expire_at + timedelta(days=days)
        service.status = VPNServiceStatus.ACTIVE.value
    elif action == "edit_config":
        service.config_link = None if text == "-" else text
    elif action == "edit_sub":
        service.subscription_link = None if text == "-" else text
    await session.commit()
    await state.clear()
    await message.answer("✅ سرویس به‌روزرسانی شد.")
    await message.answer(_format_service_detail(service), reply_markup=service_detail_keyboard(service))


@router.message(AdminBroadcastStates.text)
async def admin_broadcast_text(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("متن پیام نمی‌تواند خالی باشد.")
        return
    await state.update_data(text=text)
    await state.set_state(AdminBroadcastStates.confirm)
    await message.answer(f"آیا این پیام برای همه کاربران ارسال شود؟\n\n{text}", reply_markup=broadcast_confirm_keyboard())


@router.message(AdminBroadcastStates.confirm)
async def admin_broadcast_confirm(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    answer = (message.text or "").strip()
    if answer not in {"بله", "تایید", "✅", "ارسال"}:
        await state.clear()
        await message.answer("ارسال پیام همگانی لغو شد.", reply_markup=admin_main_keyboard())
        return
    data = await state.get_data()
    text = str(data.get("text") or "")
    users = await session.scalars(select(User.telegram_id))
    success = 0
    failed = 0
    for telegram_id in users:
        try:
            await message.bot.send_message(chat_id=telegram_id, text=text)
            success += 1
        except Exception as exc:
            failed += 1
            logger.warning("broadcast_send_failed", telegram_id=telegram_id, error=str(exc))
    await state.clear()
    await message.answer(f"📢 ارسال پیام همگانی تمام شد.\n✅ موفق: {success}\n❌ ناموفق: {failed}")


async def _save_add_plan(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    required = {"title", "duration_days", "volume_gb", "price", "sort_order"}
    if not required.issubset(data):
        await state.clear()
        await _safe_edit_or_answer(callback, "اطلاعات تعرفه کامل نیست. دوباره تلاش کنید.")
        return

    plan = await PlansRepository(session).create(
        title=str(data["title"]),
        description=data.get("description"),
        duration_days=int(data["duration_days"]),
        volume_gb=int(data["volume_gb"]),
        price=int(data["price"]),
        sort_order=int(data["sort_order"]),
        is_active=True,
    )
    await session.commit()
    await state.clear()
    if callback.message:
        await callback.message.answer(
            f"✅ تعرفه جدید ذخیره شد.\n\n{_format_plan_detail(plan)}",
            reply_markup=plan_detail_keyboard(plan),
        )


async def _save_test_account(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    required = {"title", "config_link", "duration_hours", "max_claims"}
    if not required.issubset(data):
        await state.clear()
        await _safe_edit_or_answer(callback, "اطلاعات اکانت تست کامل نیست. دوباره تلاش کنید.")
        return
    account = await TestAccountsRepository(session).create(
        title=str(data["title"]),
        description=data.get("description"),
        config_link=str(data["config_link"]),
        subscription_link=data.get("subscription_link"),
        duration_hours=int(data["duration_hours"]),
        max_claims=int(data["max_claims"]),
    )
    await session.commit()
    await state.clear()
    if callback.message:
        await callback.message.answer(
            f"✅ اکانت تست ذخیره شد.\n\n{_format_test_account_detail(account)}",
            reply_markup=test_account_detail_keyboard(account),
        )


async def _send_broadcast(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    text = str(data.get("text") or "")
    if not text:
        await state.clear()
        await _safe_edit_or_answer(callback, "متن پیام پیدا نشد.")
        return
    result = await session.scalars(select(User.telegram_id))
    success = 0
    failed = 0
    for telegram_id in result:
        try:
            await callback.bot.send_message(chat_id=telegram_id, text=text)
            success += 1
        except Exception as exc:
            failed += 1
            logger.warning("broadcast_send_failed", telegram_id=telegram_id, error=str(exc))
    await state.clear()
    if callback.message:
        await callback.message.answer(f"📢 ارسال پیام همگانی تمام شد.\n✅ موفق: {success}\n❌ ناموفق: {failed}")


async def _show_pending_payments(callback: CallbackQuery, session: AsyncSession) -> None:
    payments = await PaymentsRepository(session).list_pending_review()
    if not payments:
        text = "پرداختی در انتظار تایید نیست."
    else:
        lines = ["💳 پرداخت‌های در انتظار تایید:"]
        for payment in payments:
            order = payment.order
            user_name = payment.user.first_name or "-"
            telegram_username = f"@{payment.user.telegram_username}" if payment.user.telegram_username else "-"
            service_username = order.custom_username if order else "-"
            receipt_status = "رسید دریافت شده" if payment.receipt_file_id else "بدون رسید"
            lines.append(
                f"""
🛒 کد پیگیری: {order.tracking_code if order else "-"}
⚡ نوع سفارش: {order_kind_label(order.order_kind if order else None)}
👤 کاربر: {escape(user_name)} / {escape(telegram_username)}
🆔 آیدی عددی: {payment.user.telegram_id}
📱 موبایل: {escape(payment.user.phone_number or "-")}
⚡ پلن: {escape(order.plan.title if order and order.plan else "-")}
🔐 سرویس/نام کاربری: {escape(service_username or "-")}
💵 مبلغ: {format_money(payment.amount)} تومان
📎 وضعیت رسید: {receipt_status}"""
            )
        text = "\n".join(lines)

    await _safe_edit_or_answer(callback, text, reply_markup=pending_payments_keyboard(payments))


async def _show_pending_wallet_topups(callback: CallbackQuery, session: AsyncSession) -> None:
    transactions = await WalletTransactionsRepository(session).list_pending_topups()
    if not transactions:
        text = "شارژ کیف پول در انتظار تایید نیست."
    else:
        lines = ["🏦 شارژهای کیف پول در انتظار تایید:"]
        for transaction in transactions:
            user = transaction.user
            receipt_status = "رسید دریافت شده" if transaction.payment and transaction.payment.receipt_file_id else "بدون رسید"
            lines.append(
                f"""
👤 کاربر: {escape(user.first_name or "-")}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
💵 مبلغ: {format_money(transaction.amount)} تومان
🗓 تاریخ: {format_datetime(transaction.created_at)}
📎 وضعیت رسید: {receipt_status}
📌 وضعیت: {format_wallet_transaction_status_fa(transaction.status)}"""
            )
        text = "\n".join(lines)
    await _safe_edit_or_answer(callback, text, reply_markup=wallet_topups_keyboard(transactions))


async def _show_plans(callback: CallbackQuery, session: AsyncSession, prefix: str = "") -> None:
    plans = await PlansRepository(session).list_all()
    if not plans:
        text = f"{prefix}هنوز تعرفه‌ای ثبت نشده است."
    else:
        lines = [f"{prefix}📦 مدیریت تعرفه‌ها:"]
        for plan in plans:
            status = "فعال" if plan.is_active else "غیرفعال"
            lines.append(
                f"""
{escape(plan.title)}
وضعیت: {status}
حجم: {plan.volume_gb} گیگ | مدت: {plan.duration_days} روز | قیمت: {format_money(plan.price)} تومان
ترتیب نمایش: {plan.sort_order}"""
            )
        text = "\n".join(lines)

    await _safe_edit_or_answer(callback, text, reply_markup=plans_management_keyboard(plans))


async def _show_test_accounts(callback: CallbackQuery, session: AsyncSession, prefix: str = "") -> None:
    accounts = await TestAccountsRepository(session).list_all()
    if not accounts:
        text = f"{prefix}هنوز اکانت تستی ثبت نشده است."
    else:
        lines = [f"{prefix}🔑 مدیریت اکانت تست:"]
        for account in accounts:
            status = "فعال" if account.is_active else "غیرفعال"
            limit = "نامحدود" if account.max_claims == 0 else str(account.max_claims)
            lines.append(
                f"""
{escape(account.title)}
وضعیت: {status}
مدت: {account.duration_hours} ساعت
دریافت: {account.claim_count}/{limit}"""
            )
        text = "\n".join(lines)
    await _safe_edit_or_answer(callback, text, reply_markup=test_accounts_keyboard(accounts))


async def _show_users(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = UsersRepository(session)
    total = await repo.count_all()
    verified = await repo.count_phone_verified()
    recent = await repo.list_recent(10)
    lines = [
        "👥 مدیریت کاربران",
        f"👤 تعداد کل کاربران: {total}",
        f"📱 کاربران تایید موبایل شده: {verified}",
        "",
        "آخرین کاربران:",
    ]
    for user in recent:
        lines.append(f"{user.telegram_id} | @{user.telegram_username or '-'} | {escape(user.first_name or '-')}")
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=users_admin_keyboard(recent))


async def _show_user_detail(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    orders_count = await OrdersRepository(session).count_by_user(user.id)
    services_count = await ServicesRepository(session).count_by_user(user.id)
    viewer_id = callback.from_user.id if callback.from_user else 0
    text = f"""👤 جزئیات کاربر

🆔 آیدی عددی: {user.telegram_id}
🔗 یوزرنیم: @{escape(user.telegram_username or "-")}
👤 نام: {escape(user.first_name or "-")}
📱 موبایل: {escape(user.phone_number or "-")}
🏦 موجودی کیف پول: {format_money(user.wallet_balance)} تومان
🛠 ادمین: {"بله" if user.is_admin else "خیر"}
🗓 تاریخ عضویت: {format_datetime(user.created_at)}
🧾 تعداد سفارش‌ها: {orders_count}
🛍 تعداد سرویس‌ها: {services_count}"""
    await _safe_edit_or_answer(callback, text, reply_markup=user_detail_keyboard(user, viewer_id=viewer_id))


async def _show_user_orders(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    orders = await OrdersRepository(session).list_by_user(user.id)
    if not orders:
        await _safe_edit_or_answer(callback, "این کاربر سفارشی ندارد.")
        return
    lines = [f"🧾 سفارش‌های {escape(user.first_name or str(user.telegram_id))}"]
    for order in orders[:10]:
        lines.append(f"{order.tracking_code} | {order_kind_label(order.order_kind)} | {format_money(order.amount)} تومان | {order.status}")
    await _safe_edit_or_answer(callback, "\n".join(lines))


async def _show_user_services(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        await _safe_edit_or_answer(callback, "این کاربر سرویسی ندارد.")
        return
    lines = [f"🛍 سرویس‌های {escape(user.first_name or str(user.telegram_id))}"]
    for service in services[:10]:
        lines.append(f"{service.username} | {format_service_status_fa(service.status)} | انقضا: {format_datetime(service.expire_at)}")
    await _safe_edit_or_answer(callback, "\n".join(lines))


async def _show_services(callback: CallbackQuery, session: AsyncSession) -> None:
    services = await ServicesRepository(session).list_recent(10)
    if not services:
        text = "هنوز سرویسی ثبت نشده است."
    else:
        lines = ["🛍 مدیریت سرویس‌ها", "آخرین سرویس‌ها:"]
        for service in services:
            lines.append(f"{service.username} | {format_service_status_fa(service.status)} | {format_datetime(service.expire_at)}")
        text = "\n".join(lines)
    await _safe_edit_or_answer(callback, text, reply_markup=services_admin_keyboard(services))


async def _show_service_detail(callback: CallbackQuery, service) -> None:
    await _safe_edit_or_answer(callback, _format_service_detail(service), reply_markup=service_detail_keyboard(service))


async def _show_recent_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    result = await session.scalars(
        select(Order)
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    orders = list(result.all())
    if not orders:
        await _safe_edit_or_answer(callback, "هنوز سفارشی ثبت نشده است.")
        return
    lines = ["🧾 آخرین سفارش‌ها"]
    for order in orders:
        lines.append(f"{order.tracking_code} | {order_kind_label(order.order_kind)} | {format_money(order.amount)} تومان | {order.status}")
    await _safe_edit_or_answer(callback, "\n".join(lines))


async def _show_dice(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    winners = await DiceRollsRepository(session).list_recent_winners(10)
    lines = [
        "🎲 وضعیت گردونه شانس",
        f"🎁 درصد تخفیف برد: {settings.dice_win_discount_percent}٪",
        f"⏳ فاصله تلاش: {settings.dice_cooldown_hours} ساعت",
        "",
        "آخرین برنده‌ها:",
    ]
    if not winners:
        lines.append("هنوز برنده‌ای ثبت نشده است.")
    for roll in winners:
        user = roll.user
        lines.append(f"{user.telegram_id} | {roll.discount_code} | {roll.discount_percent}٪ | استفاده شده: {'بله' if roll.used else 'خیر'}")
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=admin_main_keyboard())


async def _show_settings(callback: CallbackQuery, settings: Settings) -> None:
    card = settings.payment_card_number
    masked_card = f"{card[:4]}****{card[-4:]}" if len(card) >= 8 else ("ثبت نشده" if not card else "****")
    text = f"""⚙️ تنظیمات

این مقادیر از فایل .env خوانده می‌شوند و از داخل ربات فقط نمایش داده می‌شوند.

پشتیبانی: @{escape(settings.support_username)}
شماره کارت: {escape(masked_card)}
نام صاحب کارت: {escape(settings.payment_card_holder or "-")}
پاداش زیرمجموعه‌گیری: {format_money(settings.referral_reward_amount)} تومان
حداقل شارژ کیف پول: {format_money(settings.wallet_min_topup_amount)} تومان
حداکثر شارژ کیف پول: {"بدون محدودیت" if settings.wallet_max_topup_amount == 0 else format_money(settings.wallet_max_topup_amount) + " تومان"}
درصد تخفیف تاس: {settings.dice_win_discount_percent}٪
فاصله تلاش تاس: {settings.dice_cooldown_hours} ساعت"""
    await _safe_edit_or_answer(callback, text, reply_markup=admin_main_keyboard())


async def _show_plan_detail(callback: CallbackQuery, plan) -> None:
    if plan is None:
        await _safe_edit_or_answer(callback, "تعرفه پیدا نشد.")
        return
    await _safe_edit_or_answer(callback, _format_plan_detail(plan), reply_markup=plan_detail_keyboard(plan))


async def _is_admin(telegram_id: int | None, session: AsyncSession, settings: Settings) -> bool:
    if telegram_id is None:
        return False
    if telegram_id in settings.admin_ids:
        return True
    user = await UsersRepository(session).get_by_telegram_id(telegram_id)
    return bool(user and user.is_admin)


async def _guard_admin_message(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> bool:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        await state.clear()
        await message.answer("دسترسی ندارید.")
        return False
    if (message.text or "").strip() in {texts.BTN_BACK, texts.BTN_MAIN_MENU}:
        await state.clear()
        await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        return False
    if texts.is_admin_menu_text(message.text):
        await state.clear()
        await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        return False
    return True


def _format_plan_detail(plan) -> str:
    status = "فعال" if plan.is_active else "غیرفعال"
    description = plan.description or "-"
    return f"""📦 جزئیات تعرفه

⚡ عنوان: {escape(plan.title)}
📝 توضیحات: {escape(description)}
📦 حجم: {plan.volume_gb} گیگ
🗓 مدت: {plan.duration_days} روز
💵 قیمت: {format_money(plan.price)} تومان
🔢 ترتیب نمایش: {plan.sort_order}
📌 وضعیت: {status}"""


def _format_plan_data_summary(data: dict) -> str:
    description = data.get("description") or "-"
    return f"""🧾 خلاصه تعرفه جدید

⚡ عنوان: {escape(str(data["title"]))}
📝 توضیحات: {escape(str(description))}
📦 حجم: {data["volume_gb"]} گیگ
🗓 مدت: {data["duration_days"]} روز
💵 قیمت: {format_money(int(data["price"]))} تومان
🔢 ترتیب نمایش: {data["sort_order"]}

آیا ذخیره شود؟"""


def _format_test_account_detail(account) -> str:
    status = "فعال" if account.is_active else "غیرفعال"
    limit = "نامحدود" if account.max_claims == 0 else str(account.max_claims)
    return f"""🔑 جزئیات اکانت تست

عنوان: {escape(account.title)}
توضیحات: {escape(account.description or "-")}
مدت تست: {account.duration_hours} ساعت
حداکثر دریافت: {limit}
تعداد دریافت شده: {account.claim_count}
وضعیت: {status}

لینک کانفیگ:
{escape(account.config_link)}

لینک اشتراک:
{escape(account.subscription_link or "-")}"""


def _format_test_account_data_summary(data: dict) -> str:
    limit = "نامحدود" if int(data["max_claims"]) == 0 else str(data["max_claims"])
    return f"""آیا اکانت تست زیر ثبت شود؟

عنوان: {escape(str(data["title"]))}
توضیحات: {escape(str(data.get("description") or "-"))}
مدت تست: {data["duration_hours"]} ساعت
حداکثر دریافت: {limit}

لینک کانفیگ:
{escape(str(data["config_link"]))}

لینک اشتراک:
{escape(str(data.get("subscription_link") or "-"))}"""


def _format_service_detail(service) -> str:
    user = service.user
    return f"""🛍 جزئیات سرویس

کاربر: {escape(user.first_name or "-")} | {user.telegram_id}
پلن: {escape(service.plan.title if service.plan else "-")}
نام کاربری: {escape(service.username)}
حجم: {service.volume_gb} گیگ
انقضا: {format_datetime(service.expire_at)}
وضعیت: {format_service_status_fa(service.status)}

لینک کانفیگ:
{escape(service.config_link or "-")}

لینک اشتراک:
{escape(service.subscription_link or "-")}"""


def _approved_message(result: ApprovedPaymentResult) -> str:
    if result.order_kind == OrderKind.RENEWAL.value:
        expire_at = _format_datetime(result.new_expire_at)
        return f"""✅ تمدید سرویس شما با موفقیت انجام شد

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن تمدید: {escape(result.plan_title)}
📦 حجم افزوده: {result.volume_gb} گیگ
🗓 اعتبار افزوده: {result.duration_days} روز
📅 تاریخ انقضای جدید: {expire_at}"""

    return f"""✅ پرداخت شما تایید شد

✅ سرویس شما با موفقیت ساخته شد

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن: {escape(result.plan_title)}
📦 حجم: {result.volume_gb} گیگ
🗓 اعتبار: {result.duration_days} روز

🔗 کانفیگ شما:
{escape(result.config_link or "-")}

🔗 لینک اشتراک:
{escape(result.subscription_link or "-")}"""


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")


def _parse_positive_int(value: str | None) -> int | None:
    parsed = _parse_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().replace(",", "")
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


_INVALID = object()


def _validate_edit_value(value: str | None, validator: str):
    text = (value or "").strip()
    if validator == "title":
        return text if text else _INVALID
    if validator == "description":
        return None if text == "-" else text
    if validator == "positive_int":
        return _parse_positive_int(text) or _INVALID
    if validator == "int":
        parsed = _parse_int(text)
        return parsed if parsed is not None else _INVALID
    return _INVALID


def _validation_error(validator: str) -> str:
    if validator == "title":
        return "عنوان نمی‌تواند خالی باشد."
    if validator == "positive_int":
        return "لطفاً یک عدد صحیح مثبت ارسال کنید."
    if validator == "int":
        return "لطفاً یک عدد صحیح ارسال کنید."
    return "مقدار وارد شده معتبر نیست."


async def _safe_edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            await callback.message.answer(text, reply_markup=reply_markup)


async def _remove_admin_buttons(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
