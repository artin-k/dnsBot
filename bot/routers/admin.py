from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.models import (
    AffiliateBeneficiaryType,
    AffiliateCommission,
    AffiliateCommissionStatus,
    ConfigInventory,
    ConfigInventoryStatus,
    Order,
    OrderKind,
    Payment,
    User,
    VPNServiceStatus,
    WalletTransactionStatus,
    WalletTransactionType,
    WalletWithdrawalRequest,
    WalletWithdrawalStatus,
)
from app.repositories.config_inventory import ConfigInventoryRepository
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.test_accounts import TestAccountsRepository
from app.repositories.users import UsersRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.repositories.wallet_withdrawals import WalletWithdrawalsRepository
from app.services.order_status import order_kind_label
from app.services.affiliate_service import AffiliateService
from app.services.inventory_service import (
    ConfigInventoryValidationError,
    get_available_count,
    normalize_inventory_link,
    notify_admins_empty_inventory_attempt,
    notify_admins_low_or_empty_inventory,
    release_expired_reservations,
)
from app.services.payment_service import (
    ApprovedPaymentResult,
    PaymentAlreadyProcessedError,
    PaymentApprovalError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.settings_service import (
    PAYMENT_CARD_HOLDER,
    PAYMENT_CARD_NUMBER,
    PAYMENT_DESCRIPTION,
    ORDER_EXPIRE_MINUTES,
    REFERRAL_REWARD_AMOUNT,
    SETTING_DEFINITION_BY_KEY,
    SETTING_DEFINITIONS,
    SUPPORT_USERNAME,
    WALLET_MAX_TOPUP_AMOUNT,
    WALLET_MAX_WITHDRAW_AMOUNT,
    WALLET_MIN_TOPUP_AMOUNT,
    WALLET_MIN_WITHDRAW_AMOUNT,
    AppSettingsService,
    SettingValidationError,
)
from app.services.wallet_service import WalletService, WalletTopupAlreadyProcessedError, WalletTopupError
from app.services.wallet_withdrawal_service import (
    WalletWithdrawalAlreadyProcessedError,
    WalletWithdrawalError,
    WalletWithdrawalService,
)
from app.services.vpn_panel import VPNPanelService
from app.utils.formatting import (
    format_commission_status_fa,
    format_datetime,
    format_money,
    format_order_status_fa,
    format_percent,
    format_service_status_fa,
    format_user_display,
    format_wallet_transaction_status_fa,
)
from app.utils.withdrawals import (
    format_withdrawal_destination_fa,
    format_withdrawal_status_fa,
    mask_destination,
)
from bot import texts
from bot.keyboards.admin import (
    AdminActionCallback,
    AdminAffiliateCallback,
    AdminInventoryCallback,
    AdminPaymentCallback,
    AdminPlanCallback,
    AdminServiceCallback,
    AdminSettingCallback,
    AdminTestAccountCallback,
    AdminUserCallback,
    AdminWithdrawalCallback,
    add_plan_confirm_keyboard,
    add_test_account_confirm_keyboard,
    admin_communications_keyboard,
    admin_payments_keyboard,
    admin_sales_keyboard,
    admin_services_keyboard,
    admin_settings_keyboard,
    admin_users_affiliate_keyboard,
    affiliate_commissions_keyboard,
    affiliate_management_keyboard,
    affiliate_orders_keyboard,
    affiliate_payout_confirm_keyboard,
    affiliate_search_results_keyboard,
    affiliate_tree_keyboard,
    affiliate_user_detail_keyboard,
    admin_main_keyboard,
    attach_orphans_confirm_keyboard,
    bot_settings_keyboard,
    broadcast_confirm_keyboard,
    inventory_detail_keyboard,
    inventory_list_keyboard,
    inventory_main_keyboard,
    inventory_plan_select_keyboard,
    inventory_search_results_keyboard,
    inventory_status_filter_keyboard,
    pending_payments_keyboard,
    plan_delete_confirm_keyboard,
    plan_detail_keyboard,
    plans_management_keyboard,
    service_detail_keyboard,
    setting_edit_keyboard,
    services_admin_keyboard,
    test_account_detail_keyboard,
    test_accounts_keyboard,
    user_detail_keyboard,
    users_admin_keyboard,
    wallet_withdrawal_review_keyboard,
    wallet_withdrawals_keyboard,
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
    AdminInventoryAddStates,
    AdminInventoryBulkStates,
    AdminInventoryEditStates,
    AdminInventorySearchStates,
    AdminSearchStates,
    AdminServiceEditStates,
    AdminSettingsStates,
    AdminWalletAdjustStates,
    AdminWithdrawalStates,
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
    await _ensure_admin_user_record(message.from_user, session, settings)
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
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    action = callback_data.action
    await callback.answer()
    await _ensure_admin_user_record(callback.from_user, session, settings)

    if action in {"panel", "back"}:
        await state.clear()
        if action == "back":
            if callback.message:
                await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(is_admin=True))
        elif callback.message:
            await callback.message.edit_text(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        return

    if action == "cat_sales":
        await state.clear()
        await _safe_edit_or_answer(callback, "📦 فروش و تعرفه‌ها", reply_markup=admin_sales_keyboard())
        return

    if action == "cat_users":
        await state.clear()
        await _safe_edit_or_answer(callback, "👥 کاربران و زیرمجموعه‌ها", reply_markup=admin_users_affiliate_keyboard())
        return

    if action == "cat_payments":
        await state.clear()
        await _safe_edit_or_answer(callback, "💳 پرداخت‌ها و کیف پول", reply_markup=admin_payments_keyboard())
        return

    if action == "cat_services":
        await state.clear()
        await _safe_edit_or_answer(callback, "🛍 سرویس‌ها", reply_markup=admin_services_keyboard())
        return

    if action == "cat_comms":
        await state.clear()
        await _safe_edit_or_answer(callback, "📣 ارتباطات", reply_markup=admin_communications_keyboard())
        return

    if action == "cat_settings":
        await state.clear()
        await _safe_edit_or_answer(callback, "⚙️ تنظیمات", reply_markup=admin_settings_keyboard())
        return

    if action == "affiliate":
        await state.clear()
        await _show_affiliate_management(callback)
        return

    if action == "payments":
        await state.clear()
        await _show_pending_payments(callback, session)
        return

    if action == "wallet_topups":
        await state.clear()
        await _show_pending_wallet_topups(callback, session)
        return

    if action == "wallet_withdrawals":
        await state.clear()
        await _show_wallet_withdrawals(callback, session)
        return

    if action == "plans":
        await state.clear()
        await _show_plans(callback, session)
        return

    if action == "inventory":
        await state.clear()
        await _show_inventory_main(callback)
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

    if action == "sales_report":
        await state.clear()
        await _show_sales_report(callback, session)
        return

    if action == "wallet_transactions":
        await state.clear()
        await _show_wallet_transactions(callback, session)
        return

    if action == "dice":
        await state.clear()
        await _show_dice(callback, session, settings)
        return

    if action in {"tutorials_admin", "support_admin"}:
        await state.clear()
        await _safe_edit_or_answer(callback, "این بخش فعلاً از منوی کاربر قابل مدیریت است.", reply_markup=admin_communications_keyboard())
        return

    if action == "settings":
        if not _is_env_admin(callback.from_user.id if callback.from_user else None, settings):
            await _safe_edit_or_answer(callback, "⛔ فقط مدیران ثبت‌شده در ADMIN_IDS می‌توانند تنظیمات ربات را تغییر دهند.")
            return
        await state.clear()
        await _show_settings(callback, session)
        return
    
    if action == "open_channels_menu":
            await state.clear()
            # Import the menu generator from our new file and trigger it
            from bot.routers.mandatory_channels import cmd_admin_channels
            await cmd_admin_channels(callback, session, settings)
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


@router.callback_query(AdminSettingCallback.filter())
async def admin_setting_action(
    callback: CallbackQuery,
    callback_data: AdminSettingCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not _is_env_admin(callback.from_user.id if callback.from_user else None, settings):
        await callback.answer("⛔ شما دسترسی تغییر تنظیمات را ندارید.", show_alert=True)
        return

    await callback.answer()
    action = callback_data.action

    if action in {"list", "cancel"}:
        await state.clear()
        await _show_settings(callback, session)
        return

    if action == "edit":
        definition = SETTING_DEFINITION_BY_KEY.get(callback_data.key)
        if definition is None:
            await _safe_edit_or_answer(callback, "تنظیم انتخاب‌شده معتبر نیست.", reply_markup=bot_settings_keyboard())
            return

        values = await AppSettingsService(session).get_all_settings()
        current_value = values.get(definition.key, definition.default)
        await state.set_state(AdminSettingsStates.value)
        await state.update_data(setting_key=definition.key)
        prompt = _format_setting_prompt(definition.key, current_value)
        if callback.message:
            await callback.message.answer(prompt, reply_markup=setting_edit_keyboard())
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.", reply_markup=bot_settings_keyboard())


@router.callback_query(AdminInventoryCallback.filter())
async def admin_inventory_action(
    callback: CallbackQuery,
    callback_data: AdminInventoryCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    action = callback_data.action

    if action == "summary":
        await state.clear()
        await _show_inventory_summary(callback, session)
        return
    if action == "low":
        await state.clear()
        await _show_low_inventory(callback, session, settings)
        return
    if action == "add_plan":
        await state.clear()
        await _show_inventory_plan_select(callback, session, "add")
        return
    if action == "bulk_plan":
        await state.clear()
        await _show_inventory_plan_select(callback, session, "bulk")
        return
    if action == "list_plan":
        await state.clear()
        await _show_inventory_plan_select(callback, session, "list_status")
        return
    if action == "add":
        await state.clear()
        await state.set_state(AdminInventoryAddStates.config_link)
        await state.update_data(plan_id=callback_data.plan_id)
        if callback.message:
            await callback.message.answer("لینک کانفیگ را ارسال کنید:")
        return
    if action == "bulk":
        await state.clear()
        await state.set_state(AdminInventoryBulkStates.text)
        await state.update_data(plan_id=callback_data.plan_id)
        if callback.message:
            await callback.message.answer(
                """لطفاً کانفیگ‌ها را ارسال کنید.
هر کانفیگ در یک خط باشد.
اگر لینک اشتراک هم دارید، هر خط را به این شکل ارسال کنید:
config_link | subscription_link"""
            )
        return
    if action == "list_status":
        await state.clear()
        await _safe_edit_or_answer(callback, "وضعیت مورد نظر را انتخاب کنید:", reply_markup=inventory_status_filter_keyboard(callback_data.plan_id))
        return
    if action == "list":
        await state.clear()
        await _show_inventory_list(callback, session, plan_id=callback_data.plan_id or None, status=callback_data.status, page=callback_data.page)
        return
    if action == "detail":
        await state.clear()
        await _show_inventory_detail(callback, session, callback_data.item_id)
        return
    if action == "search":
        await state.clear()
        await state.set_state(AdminInventorySearchStates.query)
        if callback.message:
            await callback.message.answer("شناسه، لینک، عنوان، نام کاربری یا یادداشت کانفیگ را ارسال کنید:")
        return

    item = await ConfigInventoryRepository(session).get(callback_data.item_id)
    if item is None:
        await _safe_edit_or_answer(callback, "کانفیگ پیدا نشد.", reply_markup=inventory_main_keyboard())
        return

    if action == "disable":
        if item.status == ConfigInventoryStatus.AVAILABLE.value:
            item.status = ConfigInventoryStatus.DISABLED.value
            await session.commit()
        await _show_inventory_detail(callback, session, item.id)
        return
    if action == "enable":
        if item.status == ConfigInventoryStatus.DISABLED.value:
            item.status = ConfigInventoryStatus.AVAILABLE.value
            await session.commit()
        await _show_inventory_detail(callback, session, item.id)
        return
    if action == "delete":
        if item.status not in {ConfigInventoryStatus.AVAILABLE.value, ConfigInventoryStatus.DISABLED.value}:
            await callback.answer("کانفیگ فروخته‌شده یا رزروشده قابل حذف نیست.", show_alert=True)
            return
        await session.delete(item)
        await session.commit()
        await _safe_edit_or_answer(callback, "✅ کانفیگ حذف شد.", reply_markup=inventory_main_keyboard())
        return
    if action in {"edit_config", "edit_sub", "edit_note"}:
        await state.set_state(AdminInventoryEditStates.value)
        await state.update_data(item_id=item.id, field=action)
        prompts = {
            "edit_config": "لینک کانفیگ جدید را ارسال کنید:",
            "edit_sub": "لینک اشتراک جدید را ارسال کنید یا برای خالی بودن - بفرستید:",
            "edit_note": "یادداشت جدید را ارسال کنید یا برای خالی بودن - بفرستید:",
        }
        if callback.message:
            await callback.message.answer(prompts[action])
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.", reply_markup=inventory_main_keyboard())


@router.callback_query(AdminAffiliateCallback.filter())
async def admin_affiliate_action(
    callback: CallbackQuery,
    callback_data: AdminAffiliateCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    await _ensure_admin_user_record(callback.from_user, session, settings)
    service = AffiliateService(session, settings)
    action = callback_data.action

    if action == "summary":
        await state.clear()
        await _show_affiliate_summary(callback, service)
        return
    if action == "tree":
        await state.clear()
        await _show_affiliate_tree(callback, service, callback_data.user_id, callback_data.page)
        return
    if action == "search":
        await state.set_state(AdminSearchStates.affiliate_user_query)
        if callback.message:
            await callback.message.answer("لطفاً آیدی عددی، یوزرنیم، شماره موبایل یا کد دعوت کاربر را ارسال کنید:")
        return
    if action == "detail":
        await state.clear()
        user = await session.get(User, callback_data.user_id)
        if user is None:
            await _safe_edit_or_answer(callback, "کاربر پیدا نشد.", reply_markup=affiliate_management_keyboard())
            return
        await _show_affiliate_user_detail(callback, service, user)
        return
    if action == "user_orders":
        await state.clear()
        user = await session.get(User, callback_data.user_id)
        if user is None:
            await _safe_edit_or_answer(callback, "کاربر پیدا نشد.", reply_markup=affiliate_management_keyboard())
            return
        await _show_user_orders(callback, session, user, reply_markup=await _affiliate_user_keyboard(service, user))
        return
    if action == "attach_user_root":
        await state.clear()
        user = await session.get(User, callback_data.user_id)
        if user is None:
            await _safe_edit_or_answer(callback, "کاربر پیدا نشد.", reply_markup=affiliate_management_keyboard())
            return
        attached = await service.attach_user_to_root(user)
        await session.commit()
        if not attached:
            await _safe_edit_or_answer(
                callback,
                "اتصال این کاربر به ریشه قابل انجام نیست. کاربر یا معرف دارد، یا مالک ریشه تنظیم/فعال نیست.",
                reply_markup=await _affiliate_user_keyboard(service, user),
            )
            return
        await _safe_edit_or_answer(
            callback,
            "✅ کاربر به مالک ریشه متصل شد.",
            reply_markup=await _affiliate_user_keyboard(service, user),
        )
        return
    if action == "commissions":
        await state.clear()
        await _show_commissions_report(callback, service)
        return
    if action == "commissions_root":
        await state.clear()
        await _show_commissions_report(callback, service, beneficiary_type=AffiliateBeneficiaryType.ROOT_OWNER.value)
        return
    if action == "commissions_direct":
        await state.clear()
        await _show_commissions_report(callback, service, beneficiary_type=AffiliateBeneficiaryType.DIRECT_REFERRER.value)
        return
    if action == "commissions_unpaid":
        await state.clear()
        await _show_commissions_report(callback, service, status=AffiliateCommissionStatus.APPROVED.value)
        return
    if action == "orders":
        await state.clear()
        await _show_downline_orders(callback, service, page=callback_data.page)
        return
    if action == "payouts":
        await state.clear()
        await _show_commission_payouts(callback, service)
        return
    if action == "settings":
        await state.clear()
        await _show_affiliate_settings(callback, service)
        return
    if action == "attach":
        await state.clear()
        await _show_attach_orphans_confirm(callback, service)
        return
    if action == "attach_confirm":
        await state.clear()
        count = await service.attach_orphans_to_root()
        await session.commit()
        await _safe_edit_or_answer(
            callback,
            f"✅ {count} کاربر به مالک ریشه متصل شدند.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    if action == "pay":
        await state.clear()
        await _safe_edit_or_answer(
            callback,
            "آیا از تسویه کمیسیون‌های انتخاب‌شده مطمئن هستید؟",
            reply_markup=affiliate_payout_confirm_keyboard(commission_id=callback_data.commission_id),
        )
        return
    if action == "pay_confirm":
        await state.clear()
        commission = await service.mark_commission_paid(callback_data.commission_id)
        await session.commit()
        if commission is None:
            await _safe_edit_or_answer(callback, "کمیسیون پیدا نشد.", reply_markup=affiliate_management_keyboard())
            return
        await _safe_edit_or_answer(
            callback,
            f"✅ کمیسیون {commission.id} تسویه شد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    if action == "pay_all_root":
        await state.clear()
        await _safe_edit_or_answer(
            callback,
            "آیا از تسویه کمیسیون‌های انتخاب‌شده مطمئن هستید؟",
            reply_markup=affiliate_payout_confirm_keyboard(pay_all_root=True),
        )
        return
    if action == "pay_all_root_confirm":
        await state.clear()
        count = await service.mark_all_root_approved_paid()
        await session.commit()
        await _safe_edit_or_answer(
            callback,
            f"✅ {count} کمیسیون تاییدشده مالک ریشه تسویه شد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    if action == "rebuild":
        await state.clear()
        processed, created, skipped = await service.rebuild_commissions_for_completed_orders()
        await session.commit()
        await _safe_edit_or_answer(
            callback,
            f"""🔄 بازسازی کمیسیون سفارش‌های تکمیل‌شده

🧾 سفارش‌های بررسی‌شده: {processed}
✅ کمیسیون‌های ساخته‌شده: {created}
⏭ بدون تغییر/ردشده: {skipped}""",
            reply_markup=affiliate_management_keyboard(),
        )
        return

    await _safe_edit_or_answer(callback, "عملیات نامعتبر است.", reply_markup=affiliate_management_keyboard())


@router.callback_query(AdminPaymentCallback.filter())
async def admin_payment_action(
    callback: CallbackQuery,
    callback_data: AdminPaymentCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    payment_service = PaymentService(session, VPNPanelService(), settings)
    try:
        if callback_data.action == "approve":
            result = await payment_service.approve_payment(callback_data.payment_id)
            await callback.bot.send_message(
                chat_id=result.user_telegram_id,
                text=_approved_message(result),
            )
            if result.waiting_inventory:
                await callback.answer("❌ موجودی کانفیگ برای این تعرفه تمام شده است. ابتدا موجودی را شارژ کنید.", show_alert=True)
                if result.order_kind == OrderKind.PURCHASE.value and result.plan_id:
                    await notify_admins_low_or_empty_inventory(callback.bot, session, result.plan_id)
            else:
                await callback.answer("پرداخت تایید شد.")
                # Inventory belongs only to new purchases. Renewals extend the existing
                # service/config and must not trigger low-stock or empty-stock alerts.
                if result.order_kind == OrderKind.PURCHASE.value and result.plan_id:
                    await notify_admins_low_or_empty_inventory(callback.bot, session, result.plan_id)
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
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
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


@router.callback_query(AdminWithdrawalCallback.filter())
async def admin_withdrawal_action(
    callback: CallbackQuery,
    callback_data: AdminWithdrawalCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    admin_user = await _ensure_admin_user_record(callback.from_user, session, settings)
    action = callback_data.action

    if action == "detail":
        await state.clear()
        await _show_withdrawal_detail(callback, session, callback_data.withdrawal_id)
        return

    if action == "pay":
        await state.clear()
        try:
            result = await WalletWithdrawalService(session).mark_paid(
                callback_data.withdrawal_id,
                admin_user_id=admin_user.id if admin_user else None,
            )
        except WalletWithdrawalAlreadyProcessedError:
            await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        except WalletWithdrawalError:
            await callback.answer("این درخواست پیدا نشد.", show_alert=True)
            return
        await callback.bot.send_message(
            chat_id=result.user_telegram_id,
            text=f"""✅ درخواست برداشت شما پرداخت شد.

💵 مبلغ: {format_money(result.amount)} تومان
🧾 کد درخواست: {result.withdrawal.id}""",
        )
        await callback.answer("درخواست برداشت پرداخت شد.")
        await _remove_admin_buttons(callback)
        return

    if action == "reject":
        await state.set_state(AdminWithdrawalStates.reject_reason)
        await state.update_data(withdrawal_id=callback_data.withdrawal_id)
        if callback.message:
            await callback.message.answer(
                """دلیل رد درخواست را ارسال کنید.
برای بدون توضیح، - را ارسال کنید:"""
            )
        return

    await callback.answer("عملیات نامعتبر است.", show_alert=True)


@router.callback_query(AdminPlanCallback.filter())
async def admin_plan_action(
    callback: CallbackQuery,
    callback_data: AdminPlanCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin(callback.from_user.id if callback.from_user else None, session, settings):
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    await callback.answer()
    plans_repo = PlansRepository(session)
    plan = await plans_repo.get(callback_data.plan_id)
    if plan is None:
        await _safe_edit_or_answer(callback, "تعرفه پیدا نشد.")
        return

    action = callback_data.action
    if action == "detail":
        await _show_plan_detail(callback, plan, session)
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
        await _show_plan_detail(callback, refreshed, session)
        return

    if action == "delete":
        usage_note = ""
        if await plans_repo.has_usage(plan.id):
            usage_note = "\n\n⚠️ این تعرفه سفارش یا سرویس ثبت‌شده دارد؛ در صورت تایید، حذف کامل انجام نمی‌شود و فقط غیرفعال خواهد شد."
        await _safe_edit_or_answer(
            callback,
            f"""⚠️ تایید حذف تعرفه

آیا از حذف این تعرفه مطمئن هستید؟

عنوان: {escape(plan.title)}
قیمت: {format_money(plan.price)} تومان{usage_note}""",
            reply_markup=plan_delete_confirm_keyboard(plan),
        )
        return

    if action == "delete_confirm":
        if await plans_repo.has_usage(plan.id):
            await plans_repo.set_active(plan.id, False)
            await session.commit()
            refreshed = await plans_repo.get(plan.id)
            available_count = await get_available_count(session, refreshed.id) if refreshed is not None else None
            detail = _format_plan_detail(refreshed, available_count) if refreshed is not None else ""
            await _safe_edit_or_answer(
                callback,
                f"این تعرفه سفارش یا سرویس ثبت‌شده دارد؛ حذف کامل ممکن نیست و به‌جای آن غیرفعال شد.\n\n{detail}",
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
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
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
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
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
        await callback.answer("⛔ شما دسترسی مدیریت ندارید.", show_alert=True)
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
    available_count = await get_available_count(session, plan.id)
    await message.answer("✅ تعرفه به‌روزرسانی شد.")
    await message.answer(_format_plan_detail(plan, available_count), reply_markup=plan_detail_keyboard(plan))


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


@router.message(AdminSearchStates.affiliate_user_query)
async def admin_affiliate_user_search(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    users = await UsersRepository(session).search(message.text or "", limit=8)
    await state.clear()
    if not users:
        await message.answer("کاربری با این مشخصات پیدا نشد.", reply_markup=affiliate_management_keyboard())
        return
    if len(users) == 1:
        bot_info = await message.bot.get_me()
        service = AffiliateService(session, settings)
        await message.answer(
            _format_affiliate_user_detail(
                await service.user_detail(users[0]),
                bot_username=bot_info.username,
            ),
            reply_markup=await _affiliate_user_keyboard(service, users[0]),
        )
        return

    lines = ["نتایج جستجوی کاربران:"]
    for user in users:
        lines.append(f"{user.id}. {format_user_display(user)} | کد دعوت: {escape(user.referral_code or '-')}")
    await message.answer("\n".join(lines), reply_markup=affiliate_search_results_keyboard(users))


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


@router.message(AdminWithdrawalStates.reject_reason)
async def admin_withdrawal_reject_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    withdrawal_id = int(data.get("withdrawal_id") or 0)
    reason_text = (message.text or "").strip()
    reason = None if reason_text == "-" else reason_text
    admin_user = await UsersRepository(session).get_by_telegram_id(message.from_user.id) if message.from_user else None
    try:
        result = await WalletWithdrawalService(session).reject(
            withdrawal_id,
            admin_user_id=admin_user.id if admin_user else None,
            admin_note=reason,
        )
    except WalletWithdrawalAlreadyProcessedError:
        await state.clear()
        await message.answer("این درخواست قبلاً بررسی شده است.", reply_markup=admin_main_keyboard())
        return
    except WalletWithdrawalError:
        await state.clear()
        await message.answer("درخواست برداشت پیدا نشد.", reply_markup=admin_main_keyboard())
        return

    reason_line = reason or "بدون توضیح"
    await message.bot.send_message(
        chat_id=result.user_telegram_id,
        text=f"""❌ درخواست برداشت شما رد شد.

💵 مبلغ برگشتی به کیف پول: {format_money(result.amount)} تومان
دلیل: {escape(reason_line)}""",
    )
    await state.clear()
    await message.answer("✅ درخواست برداشت رد شد و مبلغ به کیف پول کاربر برگشت.", reply_markup=admin_main_keyboard())


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


@router.message(AdminSettingsStates.value)
async def admin_setting_value(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_settings_message(message, state, session, settings):
        return
    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    if key not in SETTING_DEFINITION_BY_KEY:
        await state.clear()
        await message.answer("ویرایش تنظیمات قابل ادامه نیست. دوباره تلاش کنید.", reply_markup=bot_settings_keyboard())
        return

    app_settings = AppSettingsService(session)
    try:
        await app_settings.set_setting(key, message.text or "")
    except SettingValidationError as exc:
        await message.answer(str(exc))
        return

    await session.commit()
    await state.clear()
    await message.answer("✅ تنظیم با موفقیت ذخیره شد.")
    await _send_settings(message, session)


@router.message(AdminInventoryAddStates.config_link)
async def inventory_add_config_link(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    try:
        config_link = normalize_inventory_link(message.text, required=True)
    except ConfigInventoryValidationError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(config_link=config_link)
    await state.set_state(AdminInventoryAddStates.subscription_link)
    await message.answer("لینک اشتراک را ارسال کنید یا برای خالی بودن - بفرستید:")


@router.message(AdminInventoryAddStates.subscription_link)
async def inventory_add_subscription_link(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    try:
        subscription_link = normalize_inventory_link(message.text, required=False)
    except ConfigInventoryValidationError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(subscription_link=subscription_link)
    await state.set_state(AdminInventoryAddStates.note)
    await message.answer("عنوان یا یادداشت این کانفیگ را ارسال کنید یا - بفرستید:")


@router.message(AdminInventoryAddStates.note)
async def inventory_add_note(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    plan_id = int(data.get("plan_id") or 0)
    plan = await PlansRepository(session).get(plan_id)
    if plan is None:
        await state.clear()
        await message.answer("تعرفه پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    note = (message.text or "").strip()
    note = None if note == "-" else note
    item = await ConfigInventoryRepository(session).create(
        plan_id=plan.id,
        config_link=str(data.get("config_link") or ""),
        subscription_link=data.get("subscription_link"),
        title=note,
        note=note,
    )
    await session.commit()
    await state.clear()
    await message.answer(f"✅ کانفیگ #{item.id} برای تعرفه {escape(plan.title)} ذخیره شد.", reply_markup=inventory_main_keyboard())


@router.message(AdminInventoryBulkStates.text)
async def inventory_bulk_text(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    plan_id = int(data.get("plan_id") or 0)
    plan = await PlansRepository(session).get(plan_id)
    if plan is None:
        await state.clear()
        await message.answer("تعرفه پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    created = 0
    failed = 0
    for line in (message.text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        config_part, separator, subscription_part = raw.partition("|")
        try:
            config_link = normalize_inventory_link(config_part, required=True)
            subscription_link = normalize_inventory_link(subscription_part, required=False) if separator else None
        except ConfigInventoryValidationError:
            failed += 1
            continue
        await ConfigInventoryRepository(session).create(
            plan_id=plan.id,
            config_link=config_link,
            subscription_link=subscription_link,
        )
        created += 1
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ {created} کانفیگ با موفقیت اضافه شد.\n❌ {failed} خط نامعتبر بود.",
        reply_markup=inventory_main_keyboard(),
    )


@router.message(AdminInventorySearchStates.query)
async def inventory_search_query(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    items = await ConfigInventoryRepository(session).search(message.text or "")
    await state.clear()
    if not items:
        await message.answer("کانفیگی با این مشخصات پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    lines = ["نتایج جستجوی کانفیگ‌ها:"]
    for item in items:
        lines.append(_format_inventory_list_item(item))
    await message.answer("\n".join(lines), reply_markup=inventory_search_results_keyboard(items))


@router.message(AdminInventoryEditStates.value)
async def inventory_edit_value(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    if not await _guard_admin_message(message, state, session, settings):
        return
    data = await state.get_data()
    item = await ConfigInventoryRepository(session).get(int(data.get("item_id") or 0))
    if item is None:
        await state.clear()
        await message.answer("کانفیگ پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    field = data.get("field")
    try:
        if field == "edit_config":
            item.config_link = normalize_inventory_link(message.text, required=True)
        elif field == "edit_sub":
            item.subscription_link = normalize_inventory_link(message.text, required=False)
        elif field == "edit_note":
            text = (message.text or "").strip()
            item.note = None if text == "-" else text
            item.title = item.note
    except ConfigInventoryValidationError as exc:
        await message.answer(str(exc))
        return
    if not item.config_link and not item.subscription_link:
        await message.answer("حداقل یکی از لینک کانفیگ یا لینک اشتراک باید ثبت شود.")
        return
    await session.commit()
    await state.clear()
    refreshed = await ConfigInventoryRepository(session).get_with_details(item.id)
    await message.answer("✅ کانفیگ به‌روزرسانی شد.")
    await message.answer(
        _format_inventory_detail(refreshed or item),
        reply_markup=inventory_detail_keyboard(refreshed or item),
    )


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
            f"✅ تعرفه جدید ذخیره شد.\n\n{_format_plan_detail(plan, 0)}",
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


async def _show_inventory_main(callback: CallbackQuery) -> None:
    await _safe_edit_or_answer(
        callback,
        """📦 مدیریت موجودی کانفیگ‌ها

از این بخش می‌توانید کانفیگ‌های آماده هر تعرفه را مدیریت کنید.""",
        reply_markup=inventory_main_keyboard(),
    )


async def _show_inventory_plan_select(callback: CallbackQuery, session: AsyncSession, action: str) -> None:
    plans = await PlansRepository(session).list_all()
    if not plans:
        await _safe_edit_or_answer(callback, "ابتدا یک تعرفه بسازید.", reply_markup=inventory_main_keyboard())
        return
    await _safe_edit_or_answer(callback, "تعرفه مورد نظر را انتخاب کنید:", reply_markup=inventory_plan_select_keyboard(plans, action))


async def _show_inventory_summary(callback: CallbackQuery, session: AsyncSession) -> None:
    plans = await PlansRepository(session).list_all()
    counts = await ConfigInventoryRepository(session).counts_by_plan()
    if not plans:
        await _safe_edit_or_answer(callback, "هنوز تعرفه‌ای ثبت نشده است.", reply_markup=inventory_main_keyboard())
        return
    lines = ["📊 خلاصه موجودی کانفیگ‌ها"]
    for plan in plans:
        plan_counts = counts.get(plan.id, {})
        available_count = plan_counts.get(ConfigInventoryStatus.AVAILABLE.value, 0)
        plan_status = "🟢 تعرفه فعال" if plan.is_active else "🔴 تعرفه غیرفعال"
        purchase_status = "✅ وضعیت خرید: قابل خرید" if plan.is_active and available_count > 0 else "❌ وضعیت خرید: ناموجود"
        if not plan.is_active:
            purchase_status = "⛔ وضعیت خرید: غیرفعال توسط ادمین"
        lines.append(
            f"""
⚡ {escape(plan.title)}
📌 وضعیت تعرفه: {plan_status}
🟢 آماده فروش: {available_count}
🟡 رزرو شده: {plan_counts.get(ConfigInventoryStatus.RESERVED.value, 0)}
🔴 فروخته شده: {plan_counts.get(ConfigInventoryStatus.SOLD.value, 0)}
⚫ غیرفعال: {plan_counts.get(ConfigInventoryStatus.DISABLED.value, 0)}
{purchase_status}"""
        )
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=inventory_main_keyboard())


async def _show_low_inventory(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    plans = await ConfigInventoryRepository(session).plan_ids_low_or_empty(settings.config_low_stock_threshold)
    if not plans:
        await _safe_edit_or_answer(callback, "تعرفه کم‌موجودی وجود ندارد.", reply_markup=inventory_main_keyboard())
        return
    counts = await ConfigInventoryRepository(session).available_counts_for_plans([plan.id for plan in plans])
    lines = [f"⚠️ تعرفه‌های کم‌موجودی\nآستانه هشدار: {settings.config_low_stock_threshold}"]
    for plan in plans:
        lines.append(f"⚡ {escape(plan.title)} | موجودی: {counts.get(plan.id, 0)}")
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=inventory_main_keyboard())


async def _show_inventory_list(
    callback: CallbackQuery,
    session: AsyncSession,
    *,
    plan_id: int | None,
    status: str,
    page: int,
) -> None:
    items, has_next = await ConfigInventoryRepository(session).list_items(plan_id=plan_id, status=status, page=page)
    if not items:
        await _safe_edit_or_answer(callback, "کانفیگی با این فیلتر پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    lines = [f"📋 لیست کانفیگ‌ها | صفحه {max(page, 0) + 1}"]
    for item in items:
        lines.append(_format_inventory_list_item(item))
    await _safe_edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=inventory_list_keyboard(items, plan_id=plan_id or 0, status=status, page=max(page, 0), has_next=has_next),
    )


async def _show_inventory_detail(callback: CallbackQuery, session: AsyncSession, item_id: int) -> None:
    item = await ConfigInventoryRepository(session).get_with_details(item_id)
    if item is None:
        await _safe_edit_or_answer(callback, "کانفیگ پیدا نشد.", reply_markup=inventory_main_keyboard())
        return
    await _safe_edit_or_answer(callback, _format_inventory_detail(item), reply_markup=inventory_detail_keyboard(item))


def _format_inventory_list_item(item: ConfigInventory) -> str:
    preview = _short_preview(item.config_link or item.subscription_link or "-")
    sold_to = f" | کاربر: {item.sold_to_user.telegram_id}" if item.sold_to_user else ""
    reserved = f" | سفارش: {item.reserved_by_order_id}" if item.reserved_by_order_id else ""
    return f"""
#{item.id} | {escape(item.status)}
⚡ پلن: {escape(item.plan.title if item.plan else "-")}
🔗 {escape(preview)}
🗓 {format_datetime(item.created_at)}{sold_to}{reserved}"""


def _format_inventory_detail(item: ConfigInventory) -> str:
    return f"""📦 جزئیات کانفیگ

🆔 شناسه: {item.id}
⚡ تعرفه: {escape(item.plan.title if item.plan else "-")}
📌 وضعیت: {escape(item.status)}
👤 نام کاربری موجودی: {escape(item.username or "-")}
🛒 سفارش رزرو: {item.reserved_by_order_id or "-"}
👤 کاربر خریدار: {item.sold_to_user_id or "-"}
🗓 زمان فروش: {format_datetime(item.sold_at)}
📝 یادداشت: {escape(item.note or "-")}

🔗 کانفیگ:
{escape(item.config_link or "-")}

🔗 لینک اشتراک:
{escape(item.subscription_link or "-")}"""


def _short_preview(value: str, limit: int = 60) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


async def _show_affiliate_management(callback: CallbackQuery) -> None:
    await _safe_edit_or_answer(
        callback,
        "👥 مدیریت زیرمجموعه‌ها\n\nیکی از گزارش‌ها یا عملیات زیر را انتخاب کنید:",
        reply_markup=affiliate_management_keyboard(),
    )


def _root_owner_status_text(settings: Settings, root: User | None) -> str:
    if settings.root_admin_telegram_id is None:
        return "تنظیم نشده"
    if root is None:
        return "هنوز ربات را استارت نکرده است"
    return f"{format_user_display(root)}"


def _affiliate_root_warnings(settings: Settings, root: User | None) -> str:
    if settings.root_admin_telegram_id is None:
        return "\n\n⚠️ برای فعال شدن کامل سیستم زیرمجموعه‌گیری، ROOT_ADMIN_TELEGRAM_ID را در فایل .env تنظیم کنید."
    if root is None:
        return "\n\n⚠️ مالک ریشه تنظیم شده، اما هنوز ربات را استارت نکرده است."
    return ""


async def _show_affiliate_summary(callback: CallbackQuery, service: AffiliateService) -> None:
    summary = await service.summary()
    root_name = _root_owner_status_text(service.settings, summary.root_owner)
    warnings = _affiliate_root_warnings(service.settings, summary.root_owner)
    text = f"""📊 خلاصه زیرمجموعه‌گیری

👑 مالک ریشه: {root_name}
👥 کل کاربران: {summary.total_users}
🌱 کاربران مستقیم زیرمجموعه ریشه: {summary.direct_root_users}
👥 کل کاربران زیرمجموعه ریشه: {summary.total_downline_users}
🧩 کاربران بدون معرف: {summary.orphan_users}

🛒 سفارش‌های موفق: {summary.completed_orders}
💵 فروش کل: {format_money(summary.total_revenue)} تومان
💰 کمیسیون کل مالک: {format_money(summary.root_total_commission)} تومان
🧾 کمیسیون تسویه‌شده مالک: {format_money(summary.root_paid_commission)} تومان
⏳ کمیسیون تسویه‌نشده مالک: {format_money(summary.root_unpaid_commission)} تومان
🤝 کمیسیون مستقیم کاربران: {format_money(summary.direct_referral_commissions)} تومان

امروز: {summary.today_orders} سفارش | {format_money(summary.today_revenue)} تومان
۷ روز اخیر: {summary.week_orders} سفارش | {format_money(summary.week_revenue)} تومان
این ماه: {summary.month_orders} سفارش | {format_money(summary.month_revenue)} تومان{warnings}"""
    await _safe_edit_or_answer(callback, text, reply_markup=affiliate_management_keyboard())


async def _show_affiliate_tree(
    callback: CallbackQuery,
    service: AffiliateService,
    parent_id: int,
    page: int,
) -> None:
    root = await service.get_root_owner()
    if root is None:
        await _safe_edit_or_answer(
            callback,
            "برای مشاهده درخت زیرمجموعه‌ها ابتدا مالک ریشه باید تنظیم شده و ربات را استارت کرده باشد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    parent = await service.session.get(User, parent_id) if parent_id else root
    if parent is None:
        await _safe_edit_or_answer(callback, "کاربر پیدا نشد.", reply_markup=affiliate_management_keyboard())
        return

    rows, has_next = await service.direct_referrals_with_sales(parent_id=parent.id, page=page)
    lines = [
        "🌳 درخت زیرمجموعه‌ها",
        "",
        f"{'👑' if parent.id == root.id else '👤'} {format_user_display(parent)}",
    ]
    if not rows:
        lines.append("هنوز کاربری زیرمجموعه مالک ریشه نیست." if parent.id == root.id else "زیرمجموعه مستقیمی برای این کاربر ثبت نشده است.")
    for index, (user, orders_count, revenue, children_count) in enumerate(rows, start=1 + page * 10):
        branch = "└─" if index == len(rows) + page * 10 else "├─"
        lines.append(
            f"{branch} 👤 {format_user_display(user)} | خرید موفق: {orders_count} | فروش: {format_money(revenue)} تومان | زیرمجموعه مستقیم: {children_count}"
        )
    await _safe_edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=affiliate_tree_keyboard(
            parent_id=parent.id,
            page=max(page, 0),
            has_next=has_next,
            users=[row[0] for row in rows],
        ),
    )


async def _show_affiliate_user_detail(callback: CallbackQuery, service: AffiliateService, user: User) -> None:
    detail = await service.user_detail(user)
    bot_info = await callback.bot.get_me()
    await _safe_edit_or_answer(
        callback,
        _format_affiliate_user_detail(detail, bot_username=bot_info.username),
        reply_markup=await _affiliate_user_keyboard(service, user),
    )


async def _affiliate_user_keyboard(service: AffiliateService, user: User):
    root = await service.get_root_owner()
    include_attach = root is not None and user.id != root.id and user.referred_by_id is None and not user.is_root_admin
    return affiliate_user_detail_keyboard(user.id, include_attach_to_root=include_attach)


async def _show_commissions_report(
    callback: CallbackQuery,
    service: AffiliateService,
    *,
    beneficiary_type: str | None = None,
    status: str | None = None,
) -> None:
    commissions = await service.recent_commissions(limit=10, beneficiary_type=beneficiary_type, status=status)
    totals = await service.commission_totals()
    if not commissions:
        text = f"""💰 گزارش کمیسیون‌ها

جمع کل کمیسیون‌ها: {format_money(totals["total"])} تومان
پرداخت‌نشده‌ها: {format_money(totals["approved"])} تومان
تسویه‌شده‌ها: {format_money(totals["paid"])} تومان
کمیسیون مالک ریشه: {format_money(totals["root"])} تومان
کمیسیون مستقیم کاربران: {format_money(totals["direct"])} تومان

موردی برای این فیلتر پیدا نشد."""
        await _safe_edit_or_answer(callback, text, reply_markup=affiliate_commissions_keyboard([]))
        return
    lines = [
        "💰 گزارش کمیسیون‌ها",
        "",
        f"جمع کل کمیسیون‌ها: {format_money(totals['total'])} تومان",
        f"پرداخت‌نشده‌ها: {format_money(totals['approved'])} تومان",
        f"تسویه‌شده‌ها: {format_money(totals['paid'])} تومان",
        f"کمیسیون مالک ریشه: {format_money(totals['root'])} تومان",
        f"کمیسیون مستقیم کاربران: {format_money(totals['direct'])} تومان",
    ]
    for commission in commissions:
        lines.append(_format_commission_item(commission))
    await _safe_edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=affiliate_commissions_keyboard(commissions),
    )


async def _show_commission_payouts(callback: CallbackQuery, service: AffiliateService) -> None:
    root = await service.get_root_owner()
    if root is None:
        await _safe_edit_or_answer(
            callback,
            "ابتدا مالک ریشه باید تنظیم شده و ربات را استارت کرده باشد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    commissions = await service.approved_root_commissions(limit=10)
    if not commissions:
        await _safe_edit_or_answer(
            callback,
            "کمیسیون تسویه‌نشده‌ای وجود ندارد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    total = sum(commission.commission_amount for commission in commissions)
    lines = [f"🏦 تسویه کمیسیون‌ها\n\nمجموع قابل نمایش برای تسویه: {format_money(total)} تومان"]
    for commission in commissions:
        lines.append(_format_commission_item(commission))
    await _safe_edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=affiliate_commissions_keyboard(commissions, include_pay_all=True),
    )


async def _show_downline_orders(callback: CallbackQuery, service: AffiliateService, *, page: int = 0) -> None:
    orders, has_next = await service.recent_downline_orders(page=page, page_size=10)
    if not orders:
        await _safe_edit_or_answer(callback, "هنوز سفارشی برای زیرمجموعه‌ها ثبت نشده است.", reply_markup=affiliate_management_keyboard())
        return
    lines = [f"🧾 سفارش‌های زیرمجموعه‌ها\n\nصفحه {max(page, 0) + 1}"]
    for order in orders:
        buyer = order.user
        referred_by = buyer.referred_by if buyer else None
        commission_amount = int(
            await service.session.scalar(
                select(func.coalesce(func.sum(AffiliateCommission.commission_amount), 0)).where(
                    AffiliateCommission.order_id == order.id
                )
            )
            or 0
        )
        lines.append(
            f"""
👤 خریدار: {format_user_display(buyer)}
🔗 معرف: {format_user_display(referred_by)}
🛒 کد پیگیری: {order.tracking_code}
⚡ نوع: {order_kind_label(order.order_kind)}
📦 پلن: {escape(order.plan.title if order.plan else "-")}
💵 مبلغ: {format_money(order.amount)} تومان
📌 وضعیت: {format_order_status_fa(order.status)}
💰 کمیسیون: {format_money(commission_amount)} تومان
🗓 تاریخ: {format_datetime(order.created_at)}"""
        )
    await _safe_edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=affiliate_orders_keyboard(page=max(page, 0), has_next=has_next),
    )


async def _show_attach_orphans_confirm(callback: CallbackQuery, service: AffiliateService) -> None:
    root = await service.get_root_owner()
    if root is None:
        await _safe_edit_or_answer(
            callback,
            "ابتدا مالک ریشه باید تنظیم شده و ربات را استارت کرده باشد.",
            reply_markup=affiliate_management_keyboard(),
        )
        return
    count = await service.count_orphans()
    if count == 0:
        await _safe_edit_or_answer(callback, "کاربر بدون معرف وجود ندارد.", reply_markup=affiliate_management_keyboard())
        return
    await _safe_edit_or_answer(
        callback,
        f"""تعداد {count} کاربر بدون معرف پیدا شد.
آیا می‌خواهید همه آن‌ها به مالک ریشه متصل شوند؟""",
        reply_markup=attach_orphans_confirm_keyboard(),
    )


async def _show_affiliate_settings(callback: CallbackQuery, service: AffiliateService) -> None:
    settings = service.settings
    root_id = settings.root_admin_telegram_id or "-"
    root = await service.get_root_owner()
    orphan_count = await service.count_orphans()
    warnings: list[str] = []
    if settings.root_admin_telegram_id is None:
        warnings.append("برای فعال شدن کامل سیستم زیرمجموعه‌گیری، ROOT_ADMIN_TELEGRAM_ID را در فایل .env تنظیم کنید.")
    elif root is None:
        warnings.append("مالک ریشه هنوز ربات را استارت نکرده است.")
    if orphan_count > 0:
        warnings.append(f"{orphan_count} کاربر بدون معرف وجود دارد.")
    warning_text = "\n\n⚠️ هشدارها:\n" + "\n".join(f"- {item}" for item in warnings) if warnings else ""
    text = f"""⚙️ تنظیمات زیرمجموعه‌گیری

ROOT_ADMIN_TELEGRAM_ID: {root_id}
وضعیت مالک ریشه: {_root_owner_status_text(settings, root)}
OWNER_COMMISSION_PERCENT: {format_percent(settings.owner_commission_percent)}
REFERRAL_COMMISSION_PERCENT: {format_percent(settings.referral_commission_percent)}
COMMISSION_BASE: {settings.commission_base}
AFFILIATE_DEFAULT_TO_ROOT: {"فعال" if settings.affiliate_default_to_root else "غیرفعال"}

این تنظیمات از فایل .env خوانده می‌شوند. برای تغییر، فایل .env را ویرایش و ربات را مجدداً اجرا کنید.{warning_text}"""
    await _safe_edit_or_answer(callback, text, reply_markup=affiliate_management_keyboard())


async def _show_sales_report(callback: CallbackQuery, session: AsyncSession) -> None:
    row = await session.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount), 0)).where(
            Order.status == "completed"
        )
    )
    orders_count, revenue = row.one()
    await _safe_edit_or_answer(
        callback,
        f"📈 گزارش فروش\n\n🛒 سفارش‌های موفق: {int(orders_count or 0)}\n💵 فروش کل: {format_money(int(revenue or 0))} تومان",
        reply_markup=admin_sales_keyboard(),
    )


async def _show_wallet_transactions(callback: CallbackQuery, session: AsyncSession) -> None:
    transactions = await WalletTransactionsRepository(session).list_recent(limit=10)
    if not transactions:
        await _safe_edit_or_answer(callback, "هنوز تراکنش کیف پولی ثبت نشده است.", reply_markup=admin_payments_keyboard())
        return
    lines = ["📜 تراکنش‌های کیف پول"]
    for transaction in transactions:
        user = transaction.user
        lines.append(
            f"""
👤 کاربر: {format_user_display(user)}
💵 مبلغ: {format_money(transaction.amount)} تومان
📌 وضعیت: {format_wallet_transaction_status_fa(transaction.status)}
🗓 تاریخ: {format_datetime(transaction.created_at)}"""
        )
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=admin_payments_keyboard())


async def _show_pending_payments(callback: CallbackQuery, session: AsyncSession) -> None:
    released = await release_expired_reservations(session)
    if released:
        await session.commit()
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
            inventory_line = "📦 موجودی: -"
            if order and order.order_kind == OrderKind.PURCHASE.value:
                available_count = await get_available_count(session, order.plan_id)
                reserved = "بله" if order.config_inventory_id else "خیر"
                inventory_line = f"📦 کانفیگ رزرو شده: {reserved} | شناسه: {order.config_inventory_id or '-'} | موجودی پلن: {available_count}"
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
📎 وضعیت رسید: {receipt_status}
{inventory_line}"""
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


async def _show_wallet_withdrawals(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = WalletWithdrawalsRepository(session)
    pending = await repo.list_pending(limit=10)
    recent = await repo.list_recent(limit=10)
    if not pending and not recent:
        await _safe_edit_or_answer(callback, "درخواست برداشتی ثبت نشده است.", reply_markup=admin_payments_keyboard())
        return

    lines = ["💸 درخواست‌های برداشت"]
    if pending:
        lines.append("\nدر انتظار بررسی:")
        for withdrawal in pending:
            lines.append(_format_withdrawal_list_item(withdrawal))
    else:
        lines.append("\nدرخواست در انتظار بررسی وجود ندارد.")

    recent_without_pending = [item for item in recent if item.id not in {withdrawal.id for withdrawal in pending}]
    if recent_without_pending:
        lines.append("\nآخرین درخواست‌ها:")
        for withdrawal in recent_without_pending[:5]:
            user = withdrawal.user
            lines.append(
                f"""
🧾 کد: {withdrawal.id}
👤 کاربر: {escape(user.first_name or "-")} | {user.telegram_id}
💵 مبلغ: {format_money(withdrawal.amount)} تومان
📌 وضعیت: {format_withdrawal_status_fa(withdrawal.status)}
🗓 تاریخ: {format_datetime(withdrawal.created_at)}"""
            )

    keyboard_items = pending + recent_without_pending[:5]
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=wallet_withdrawals_keyboard(keyboard_items))


async def _show_withdrawal_detail(callback: CallbackQuery, session: AsyncSession, withdrawal_id: int) -> None:
    withdrawal = await WalletWithdrawalsRepository(session).get_with_details(withdrawal_id)
    if withdrawal is None:
        await _safe_edit_or_answer(callback, "درخواست برداشت پیدا نشد.", reply_markup=admin_payments_keyboard())
        return

    reply_markup = (
        wallet_withdrawal_review_keyboard(withdrawal.id)
        if withdrawal.status == WalletWithdrawalStatus.PENDING.value
        else admin_payments_keyboard()
    )
    await _safe_edit_or_answer(callback, _format_withdrawal_detail(withdrawal), reply_markup=reply_markup)


def _format_withdrawal_list_item(withdrawal: WalletWithdrawalRequest) -> str:
    user = withdrawal.user
    return f"""
🧾 کد: {withdrawal.id}
👤 کاربر: {escape(user.first_name or "-")} | {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
💵 مبلغ: {format_money(withdrawal.amount)} تومان
روش دریافت: {format_withdrawal_destination_fa(withdrawal.destination_type)}
شماره مقصد: {escape(mask_destination(withdrawal.destination_type, withdrawal.destination_number))}
🗓 تاریخ: {format_datetime(withdrawal.created_at)}"""


async def _show_plans(callback: CallbackQuery, session: AsyncSession, prefix: str = "") -> None:
    plans = await PlansRepository(session).list_all()
    if not plans:
        text = f"""{prefix}📦 مدیریت تعرفه‌ها

هنوز تعرفه‌ای ثبت نشده است.
برای ساخت تعرفه جدید از دکمه «➕ افزودن تعرفه» استفاده کنید."""
    else:
        lines = [
            f"{prefix}📦 مدیریت تعرفه‌ها",
            "",
            "از این بخش می‌توانید تعرفه‌ها را اضافه، ویرایش، فعال/غیرفعال یا حذف کنید.",
            "برای ویرایش کامل، روی دکمه «⚙️ مدیریت» هر تعرفه بزنید.",
            "",
            "📋 لیست تعرفه‌ها:",
        ]
        for index, plan in enumerate(plans, start=1):
            status = "🟢 فعال" if plan.is_active else "🔴 غیرفعال"
            lines.append(
                f"""
{index}. {escape(plan.title)}
📌 وضعیت: {status}
📦 حجم: {plan.volume_gb} گیگ
🗓 مدت: {plan.duration_days} روز
💵 قیمت: {format_money(plan.price)} تومان
🔢 ترتیب نمایش: {plan.sort_order}"""
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


async def _show_user_orders(callback: CallbackQuery, session: AsyncSession, user: User, reply_markup=None) -> None:
    orders = await OrdersRepository(session).list_by_user(user.id)
    if not orders:
        await _safe_edit_or_answer(callback, "این کاربر سفارشی ندارد.", reply_markup=reply_markup)
        return
    lines = [f"🧾 سفارش‌های {escape(user.first_name or str(user.telegram_id))}"]
    for order in orders[:10]:
        lines.append(f"{order.tracking_code} | {order_kind_label(order.order_kind)} | {format_money(order.amount)} تومان | {order.status}")
    await _safe_edit_or_answer(callback, "\n".join(lines), reply_markup=reply_markup)


async def _show_user_services(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        await _safe_edit_or_answer(callback, "این کاربر سرویسی ندارد.")
        return
    lines = [f"🛍 سرویس‌های {escape(user.first_name or str(user.telegram_id))}"]
    for service in services[:10]:
        lines.append(f"{service.username} | {format_service_status_fa(service.status)} | انقضا: {format_datetime(service.expire_at)}")
    await _safe_edit_or_answer(callback, "\n".join(lines))


def _format_affiliate_user_detail(detail, *, bot_username: str | None = None) -> str:
    user = detail.user
    referral_link = f"https://t.me/{bot_username or 'bot'}?start={user.referral_code}"
    root_badge = "بله" if user.is_root_admin else "خیر"
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"""👤 جزئیات زیرمجموعه کاربر

🆔 آیدی عددی: {user.telegram_id}
👤 نام: {escape(user.first_name or "-")}
🔗 یوزرنیم: {escape(username)}
📱 موبایل: {escape(user.phone_number or "-")}
👑 مالک ریشه: {root_badge}
🔗 معرف: {format_user_display(detail.referred_by)}
🎟 کد دعوت: {escape(user.referral_code or "-")}
🔗 لینک دعوت: {referral_link}
🌱 دعوت مستقیم: {detail.direct_referrals}
🌳 کل زیرمجموعه‌ها: {detail.total_downline}
🧾 تعداد سفارش‌ها: {detail.orders_count}
💵 مبلغ سفارش‌های موفق: {format_money(detail.successful_orders_amount)} تومان
💰 کمیسیون مالک از این کاربر: {format_money(detail.root_commission_from_user)} تومان
🤝 کمیسیون معرف مستقیم از این کاربر: {format_money(detail.direct_commission_from_user)} تومان
🛍 تعداد سرویس‌ها: {detail.services_count}
🏦 مانده کمیسیون کاربر: {format_money(user.affiliate_balance)} تومان"""


def _format_commission_item(commission: AffiliateCommission) -> str:
    order = commission.order
    type_label = {
        AffiliateBeneficiaryType.ROOT_OWNER.value: "مالک ریشه",
        AffiliateBeneficiaryType.DIRECT_REFERRER.value: "معرف مستقیم",
        AffiliateBeneficiaryType.MANUAL.value: "دستی",
    }.get(commission.beneficiary_type, commission.beneficiary_type)
    tracking_code = order.tracking_code if order else "-"
    order_kind = order_kind_label(order.order_kind) if order else "-"
    return f"""
#{commission.id}
👤 خریدار: {format_user_display(commission.buyer)}
🎯 ذی‌نفع: {format_user_display(commission.beneficiary)}
🏷 نوع: {type_label} | سطح: {commission.level}
🛒 سفارش: {tracking_code} | {order_kind}
💵 مبنا: {format_money(commission.base_amount)} تومان
📈 درصد: {format_percent(commission.percent)}
💰 کمیسیون: {format_money(commission.commission_amount)} تومان
📌 وضعیت: {format_commission_status_fa(commission.status)}
🗓 تاریخ: {format_datetime(commission.created_at)}"""


def _format_withdrawal_detail(withdrawal: WalletWithdrawalRequest) -> str:
    user = withdrawal.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"""💸 جزئیات درخواست برداشت

🧾 کد درخواست: {withdrawal.id}
👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}

💵 مبلغ: {format_money(withdrawal.amount)} تومان
روش دریافت: {format_withdrawal_destination_fa(withdrawal.destination_type)}
شماره مقصد: {escape(withdrawal.destination_number)}
نام صاحب حساب: {escape(withdrawal.account_holder_name or "-")}
توضیحات کاربر: {escape(withdrawal.user_note or "-")}
توضیحات مدیریت: {escape(withdrawal.admin_note or "-")}

وضعیت: {format_withdrawal_status_fa(withdrawal.status)}
تاریخ ثبت: {format_datetime(withdrawal.created_at)}"""


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


async def _show_settings(callback: CallbackQuery, session: AsyncSession) -> None:
    app_settings = AppSettingsService(session)
    await app_settings.ensure_defaults()
    await session.commit()
    values = await app_settings.get_all_settings()
    await _safe_edit_or_answer(callback, _format_settings_text(values), reply_markup=bot_settings_keyboard())


async def _send_settings(message: Message, session: AsyncSession) -> None:
    app_settings = AppSettingsService(session)
    await app_settings.ensure_defaults()
    await session.commit()
    values = await app_settings.get_all_settings()
    await message.answer(_format_settings_text(values), reply_markup=bot_settings_keyboard())


def _format_settings_text(values: dict[str, str | int]) -> str:
    lines = [
        "⚙️ تنظیمات",
        "",
        "این مقادیر از پایگاه داده خوانده می‌شوند و از همین بخش قابل ویرایش هستند.",
        "",
    ]
    for definition in SETTING_DEFINITIONS:
        value = values.get(definition.key, definition.default)
        lines.append(f"{definition.label}: {_format_setting_value(definition.key, value)}")
    return "\n".join(lines)


def _format_setting_prompt(key: str, current_value: str | int) -> str:
    definition = SETTING_DEFINITION_BY_KEY[key]
    if definition.value_type == "int":
        min_hint = f"حداقل مقدار مجاز: {definition.min_value}" if definition.min_value is not None else "عدد صحیح ارسال کنید."
        if key in {WALLET_MAX_TOPUP_AMOUNT, WALLET_MAX_WITHDRAW_AMOUNT}:
            min_hint = "عدد 0 یعنی بدون محدودیت. مقدار منفی مجاز نیست."
        hint = f"لطفاً مقدار جدید را به صورت عدد صحیح ارسال کنید.\n{min_hint}"
    else:
        hint = "مقدار جدید را ارسال کنید. برای خالی کردن مقدار، - بفرستید."
        if key == SUPPORT_USERNAME:
            hint += "\nنام کاربری را بدون @ هم می‌توانید بفرستید."
    return f"""✏️ ویرایش {definition.label}

مقدار فعلی:
{_format_setting_value(key, current_value)}

{hint}"""


def _format_setting_value(key: str, value: str | int | None) -> str:
    if key == SUPPORT_USERNAME:
        text = str(value or "").strip().removeprefix("@")
        return f"@{escape(text)}" if text else "ثبت نشده"
    if key in {PAYMENT_CARD_NUMBER, PAYMENT_CARD_HOLDER, PAYMENT_DESCRIPTION}:
        text = str(value or "").strip()
        return escape(text) if text else "ثبت نشده"
    if key in {REFERRAL_REWARD_AMOUNT, WALLET_MIN_TOPUP_AMOUNT, WALLET_MIN_WITHDRAW_AMOUNT}:
        return f"{format_money(int(value or 0))} تومان"
    if key in {WALLET_MAX_TOPUP_AMOUNT, WALLET_MAX_WITHDRAW_AMOUNT}:
        parsed = int(value or 0)
        return "بدون محدودیت" if parsed == 0 else f"{format_money(parsed)} تومان"
    if key == ORDER_EXPIRE_MINUTES:
        return f"{int(value or 0)} دقیقه"
    return escape(str(value if value is not None else ""))


async def _show_plan_detail(callback: CallbackQuery, plan, session: AsyncSession | None = None) -> None:
    if plan is None:
        await _safe_edit_or_answer(callback, "تعرفه پیدا نشد.")
        return
    available_count = None
    if session is not None:
        available_count = await get_available_count(session, plan.id)
    await _safe_edit_or_answer(callback, _format_plan_detail(plan, available_count), reply_markup=plan_detail_keyboard(plan))


async def _is_admin(telegram_id: int | None, session: AsyncSession, settings: Settings) -> bool:
    if telegram_id is None:
        return False
    if settings.root_admin_telegram_id is not None and telegram_id == settings.root_admin_telegram_id:
        return True
    if telegram_id in settings.admin_ids:
        return True
    user = await UsersRepository(session).get_by_telegram_id(telegram_id)
    return bool(user and user.is_admin)


def _is_env_admin(telegram_id: int | None, settings: Settings) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids


async def _ensure_admin_user_record(
    telegram_user: TelegramUser | None,
    session: AsyncSession,
    settings: Settings,
) -> User | None:
    if telegram_user is None:
        return None
    repo = UsersRepository(session)
    user = await repo.create_or_update_from_telegram(
        telegram_id=telegram_user.id,
        telegram_username=telegram_user.username,
        first_name=telegram_user.first_name,
        is_admin=telegram_user.id in settings.admin_ids,
        is_root_admin=telegram_user.id == settings.root_admin_telegram_id,
    )
    if telegram_user.id == settings.root_admin_telegram_id:
        user = await AffiliateService(session, settings).ensure_root_owner() or user
    await session.commit()
    return user


async def _guard_admin_message(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> bool:
    if not await _is_admin(message.from_user.id if message.from_user else None, session, settings):
        await state.clear()
        await message.answer("⛔ شما دسترسی مدیریت ندارید.")
        return False
    await _ensure_admin_user_record(message.from_user, session, settings)
    if texts.is_main_menu_text(message.text):
        await state.clear()
        from bot.routers.menu import route_main_menu_text

        await route_main_menu_text(message, state, session, settings)
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


async def _guard_settings_message(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> bool:
    if not _is_env_admin(message.from_user.id if message.from_user else None, settings):
        await state.clear()
        await message.answer("⛔ شما دسترسی تغییر تنظیمات را ندارید.")
        return False
    await _ensure_admin_user_record(message.from_user, session, settings)
    if texts.is_main_menu_text(message.text):
        await state.clear()
        from bot.routers.menu import route_main_menu_text

        await route_main_menu_text(message, state, session, settings)
        return False
    text = (message.text or "").strip()
    if text in {texts.BTN_BACK, texts.BTN_MAIN_MENU, "لغو", "انصراف"}:
        await state.clear()
        await _send_settings(message, session)
        return False
    if texts.is_admin_menu_text(message.text):
        await state.clear()
        await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        return False
    return True


def _format_plan_detail(plan, available_count: int | None = None) -> str:
    status = "🟢 فعال" if plan.is_active else "🔴 غیرفعال"
    if available_count is None:
        inventory_status = "نامشخص"
    elif available_count > 0:
        inventory_status = f"✅ موجود | آماده فروش: {available_count}"
    else:
        inventory_status = "❌ ناموجود | آماده فروش: 0"
    description = plan.description or "-"
    return f"""📦 جزئیات تعرفه

🆔 شناسه: {plan.id}
📌 وضعیت تعرفه: {status}
📦 وضعیت موجودی: {inventory_status}
⚡ عنوان: {escape(plan.title)}
📝 توضیحات: {escape(description)}
📦 حجم: {plan.volume_gb} گیگ
🗓 مدت: {plan.duration_days} روز
💵 قیمت: {format_money(plan.price)} تومان
🔢 ترتیب نمایش: {plan.sort_order}

چه تغییری می‌خواهید انجام دهید؟"""


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
    if result.waiting_inventory:
        return "پرداخت شما تایید شد، اما موجودی سرویس انتخاب‌شده به پایان رسیده است. پشتیبانی به‌زودی سرویس شما را ارسال می‌کند."
    if result.order_kind == OrderKind.RENEWAL.value:
        expire_at = _format_datetime(result.new_expire_at)
        return f"""✅ تمدید سرویس شما با موفقیت انجام شد

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن تمدید: {escape(result.plan_title)}
📦 حجم افزوده: {result.volume_gb} گیگ
🗓 اعتبار افزوده: {result.duration_days} روز
📅 تاریخ انقضای جدید: {expire_at}"""

    config_line = f"\n🔗 کانفیگ شما:\n{escape(result.config_link)}" if result.config_link else ""
    subscription_line = f"\n\n🔗 لینک اشتراک:\n{escape(result.subscription_link)}" if result.subscription_link else ""
    return f"""✅ پرداخت شما تایید شد

✅ سرویس شما با موفقیت ساخته شد

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن: {escape(result.plan_title)}
📦 حجم: {result.volume_gb} گیگ
🗓 اعتبار: {result.duration_days} روز{config_line}{subscription_line}"""


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
        except Exception as exc:
            logger.warning("admin_edit_text_failed", error=str(exc))
            await callback.message.answer(text, reply_markup=reply_markup)


async def _remove_admin_buttons(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
