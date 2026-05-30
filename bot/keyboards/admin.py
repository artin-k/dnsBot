from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import (
    ConfigInventory,
    ConfigInventoryStatus,
    Payment,
    Plan,
    TestAccount,
    User,
    VPNService,
    WalletTransaction,
    WalletWithdrawalRequest,

)
from app.services.settings_service import SETTING_DEFINITIONS


class AdminActionCallback(CallbackData, prefix="adm"):
    action: str


class AdminPaymentCallback(CallbackData, prefix="adm_pay"):
    action: str
    payment_id: int


class AdminPlanCallback(CallbackData, prefix="adm_plan"):
    action: str
    plan_id: int


class AdminTestAccountCallback(CallbackData, prefix="adm_test"):
    action: str
    test_account_id: int = 0


class AdminUserCallback(CallbackData, prefix="adm_user"):
    action: str
    user_id: int = 0


class AdminServiceCallback(CallbackData, prefix="adm_svc"):
    action: str
    service_id: int = 0


class AdminAffiliateCallback(CallbackData, prefix="adm_aff"):
    action: str
    user_id: int = 0
    page: int = 0
    commission_id: int = 0


class AdminSettingCallback(CallbackData, prefix="adm_set"):
    action: str
    key: str = "_"


class AdminInventoryCallback(CallbackData, prefix="adm_inv"):
    action: str
    plan_id: int = 0
    item_id: int = 0
    page: int = 0
    status: str = "all"


class AdminWithdrawalCallback(CallbackData, prefix="adm_wd"):
    action: str
    withdrawal_id: int = 0


def admin_main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 فروش و تعرفه‌ها", callback_data=AdminActionCallback(action="cat_sales"))
    builder.button(text="👥 کاربران و زیرمجموعه‌ها", callback_data=AdminActionCallback(action="cat_users"))
    builder.button(text="💳 پرداخت‌ها و کیف پول", callback_data=AdminActionCallback(action="cat_payments"))
    builder.button(text="🛍 سرویس‌ها", callback_data=AdminActionCallback(action="cat_services"))
    builder.button(text="📣 ارتباطات", callback_data=AdminActionCallback(action="cat_comms"))
    builder.button(text="⚙️ تنظیمات", callback_data=AdminActionCallback(action="cat_settings"))
    builder.button(text="📢 مدیریت کانال‌های اجباری", callback_data=AdminActionCallback(action="open_channels_menu"))
    builder.button(text="↩️ بازگشت به ربات", callback_data=AdminActionCallback(action="back"))
    
    builder.adjust(1)
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return admin_main_keyboard()


def admin_sales_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 مدیریت تعرفه‌ها", callback_data=AdminActionCallback(action="plans"))
    builder.button(text="📦 موجودی کانفیگ‌ها", callback_data=AdminActionCallback(action="inventory"))
    builder.button(text="🧾 سفارش‌ها", callback_data=AdminActionCallback(action="orders"))
    builder.button(text="📈 گزارش فروش", callback_data=AdminActionCallback(action="sales_report"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def inventory_main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 خلاصه موجودی", callback_data=AdminInventoryCallback(action="summary"))
    builder.button(text="➕ افزودن کانفیگ", callback_data=AdminInventoryCallback(action="add_plan"))
    builder.button(text="📥 افزودن گروهی کانفیگ‌ها", callback_data=AdminInventoryCallback(action="bulk_plan"))
    builder.button(text="📋 لیست کانفیگ‌ها", callback_data=AdminInventoryCallback(action="list_plan"))
    builder.button(text="🔍 جستجوی کانفیگ", callback_data=AdminInventoryCallback(action="search"))
    builder.button(text="⚠️ تعرفه‌های کم‌موجودی", callback_data=AdminInventoryCallback(action="low"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_sales"))
    builder.adjust(1)
    return builder.as_markup()


def inventory_plan_select_keyboard(plans: list[Plan], action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(text=plan.title, callback_data=AdminInventoryCallback(action=action, plan_id=plan.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="inventory"))
    builder.adjust(1)
    return builder.as_markup()


def inventory_status_filter_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    statuses = [
        ("همه", "all"),
        ("🟢 آماده فروش", ConfigInventoryStatus.AVAILABLE.value),
        ("🟡 رزرو شده", ConfigInventoryStatus.RESERVED.value),
        ("🔴 فروخته شده", ConfigInventoryStatus.SOLD.value),
        ("⚫ غیرفعال", ConfigInventoryStatus.DISABLED.value),
    ]
    for label, status in statuses:
        builder.button(text=label, callback_data=AdminInventoryCallback(action="list", plan_id=plan_id, status=status))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="inventory"))
    builder.adjust(1)
    return builder.as_markup()


def inventory_list_keyboard(
    items: list[ConfigInventory],
    *,
    plan_id: int,
    status: str,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(
            text=f"#{item.id} | {item.status}",
            callback_data=AdminInventoryCallback(action="detail", item_id=item.id, plan_id=plan_id, status=status, page=page),
        )
    if page > 0:
        builder.button(
            text="⬅️ صفحه قبل",
            callback_data=AdminInventoryCallback(action="list", plan_id=plan_id, status=status, page=page - 1),
        )
    if has_next:
        builder.button(
            text="صفحه بعد ➡️",
            callback_data=AdminInventoryCallback(action="list", plan_id=plan_id, status=status, page=page + 1),
        )
    builder.button(text="↩️ فیلترها", callback_data=AdminInventoryCallback(action="list_status", plan_id=plan_id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="inventory"))
    builder.adjust(1)
    return builder.as_markup()


def inventory_detail_keyboard(item: ConfigInventory) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if item.status == ConfigInventoryStatus.AVAILABLE.value:
        builder.button(text="⚫ غیرفعال کردن", callback_data=AdminInventoryCallback(action="disable", item_id=item.id))
    elif item.status == ConfigInventoryStatus.DISABLED.value:
        builder.button(text="🟢 فعال کردن", callback_data=AdminInventoryCallback(action="enable", item_id=item.id))
    if item.status in {ConfigInventoryStatus.AVAILABLE.value, ConfigInventoryStatus.DISABLED.value}:
        builder.button(text="🗑 حذف", callback_data=AdminInventoryCallback(action="delete", item_id=item.id))
    builder.button(text="✏️ ویرایش لینک کانفیگ", callback_data=AdminInventoryCallback(action="edit_config", item_id=item.id))
    builder.button(text="✏️ ویرایش لینک اشتراک", callback_data=AdminInventoryCallback(action="edit_sub", item_id=item.id))
    builder.button(text="✏️ ویرایش یادداشت", callback_data=AdminInventoryCallback(action="edit_note", item_id=item.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminInventoryCallback(action="list", plan_id=item.plan_id))
    builder.adjust(1)
    return builder.as_markup()


def inventory_search_results_keyboard(items: list[ConfigInventory]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(text=f"#{item.id} | {item.status}", callback_data=AdminInventoryCallback(action="detail", item_id=item.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="inventory"))
    builder.adjust(1)
    return builder.as_markup()


def admin_users_affiliate_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 کاربران", callback_data=AdminActionCallback(action="users"))
    builder.button(text="👥 مدیریت زیرمجموعه‌ها", callback_data=AdminActionCallback(action="affiliate"))
    builder.button(text="💰 گزارش کمیسیون‌ها", callback_data=AdminAffiliateCallback(action="commissions"))
    builder.button(text="🧩 اتصال کاربران بدون معرف به ریشه", callback_data=AdminAffiliateCallback(action="attach"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def admin_payments_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 پرداخت‌های در انتظار تایید", callback_data=AdminActionCallback(action="payments"))
    builder.button(text="🏦 شارژهای کیف پول", callback_data=AdminActionCallback(action="wallet_topups"))
    builder.button(text="💸 درخواست‌های برداشت", callback_data=AdminActionCallback(action="wallet_withdrawals"))
    builder.button(text="📜 تراکنش‌های کیف پول", callback_data=AdminActionCallback(action="wallet_transactions"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def admin_services_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 لیست سرویس‌ها", callback_data=AdminActionCallback(action="services"))
    builder.button(text="🔎 جستجوی سرویس", callback_data=AdminServiceCallback(action="search"))
    builder.button(text="🔑 اکانت تست", callback_data=AdminActionCallback(action="test_accounts"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def admin_communications_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 پیام همگانی", callback_data=AdminActionCallback(action="broadcast"))
    builder.button(text="📚 آموزش‌ها", callback_data=AdminActionCallback(action="tutorials_admin"))
    builder.button(text="☎️ پشتیبانی", callback_data=AdminActionCallback(action="support_admin"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ تنظیمات", callback_data=AdminActionCallback(action="settings"))
    builder.button(text="⚙️ تنظیمات زیرمجموعه‌گیری", callback_data=AdminAffiliateCallback(action="settings"))
    builder.button(text="🎲 تنظیمات گردونه شانس", callback_data=AdminActionCallback(action="dice"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def bot_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for definition in SETTING_DEFINITIONS:
        builder.button(
            text=f"✏️ {definition.label}",
            callback_data=AdminSettingCallback(action="edit", key=definition.key),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_settings"))
    builder.adjust(1)
    return builder.as_markup()


def setting_edit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ بازگشت به تنظیمات", callback_data=AdminSettingCallback(action="list"))
    builder.button(text="❌ لغو", callback_data=AdminSettingCallback(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_management_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 خلاصه زیرمجموعه‌گیری", callback_data=AdminAffiliateCallback(action="summary"))
    builder.button(text="🌳 درخت زیرمجموعه‌ها", callback_data=AdminAffiliateCallback(action="tree", page=0))
    builder.button(text="👤 جستجوی کاربر", callback_data=AdminAffiliateCallback(action="search"))
    builder.button(text="💰 گزارش کمیسیون‌ها", callback_data=AdminAffiliateCallback(action="commissions"))
    builder.button(text="🧾 سفارش‌های زیرمجموعه‌ها", callback_data=AdminAffiliateCallback(action="orders"))
    builder.button(text="🏦 تسویه کمیسیون‌ها", callback_data=AdminAffiliateCallback(action="payouts"))
    builder.button(text="⚙️ تنظیمات زیرمجموعه‌گیری", callback_data=AdminAffiliateCallback(action="settings"))
    builder.button(text="🧩 اتصال کاربران بدون معرف به ریشه", callback_data=AdminAffiliateCallback(action="attach"))
    builder.button(text="🔄 بازسازی کمیسیون سفارش‌های تکمیل‌شده", callback_data=AdminAffiliateCallback(action="rebuild"))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_users"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_tree_keyboard(*, parent_id: int, page: int, has_next: bool, users: list[User] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users or []:
        label = user.telegram_username or user.first_name or str(user.telegram_id)
        builder.button(
            text=f"👤 مشاهده {label}",
            callback_data=AdminAffiliateCallback(action="detail", user_id=user.id),
        )
    if page > 0:
        builder.button(
            text="⬅️ صفحه قبل",
            callback_data=AdminAffiliateCallback(action="tree", user_id=parent_id, page=page - 1),
        )
    if has_next:
        builder.button(
            text="صفحه بعد ➡️",
            callback_data=AdminAffiliateCallback(action="tree", user_id=parent_id, page=page + 1),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_user_detail_keyboard(user_id: int, *, include_attach_to_root: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🌳 زیرمجموعه‌های این کاربر", callback_data=AdminAffiliateCallback(action="tree", user_id=user_id))
    builder.button(text="🧾 سفارش‌های کاربر", callback_data=AdminAffiliateCallback(action="user_orders", user_id=user_id))
    if include_attach_to_root:
        builder.button(text="🧩 اتصال به ریشه", callback_data=AdminAffiliateCallback(action="attach_user_root", user_id=user_id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_search_results_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        label = user.telegram_username or user.first_name or str(user.telegram_id)
        builder.button(
            text=f"👤 {label}",
            callback_data=AdminAffiliateCallback(action="detail", user_id=user.id),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_commissions_keyboard(commissions: list, *, include_pay_all: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="فقط مالک ریشه", callback_data=AdminAffiliateCallback(action="commissions_root"))
    builder.button(text="فقط مستقیم کاربران", callback_data=AdminAffiliateCallback(action="commissions_direct"))
    builder.button(text="پرداخت‌نشده‌ها", callback_data=AdminAffiliateCallback(action="commissions_unpaid"))
    if include_pay_all:
        builder.button(text="✅ تسویه همه کمیسیون‌های مالک", callback_data=AdminAffiliateCallback(action="pay_all_root"))
    for commission in commissions[:8]:
        if getattr(commission, "status", "") == "approved":
            builder.button(
                text=f"✅ تسویه کمیسیون {commission.id}",
                callback_data=AdminAffiliateCallback(action="pay", commission_id=commission.id),
            )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(1)
    return builder.as_markup()


def affiliate_payout_confirm_keyboard(*, commission_id: int = 0, pay_all_root: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    action = "pay_all_root_confirm" if pay_all_root else "pay_confirm"
    builder.button(
        text="✅ تایید تسویه",
        callback_data=AdminAffiliateCallback(action=action, commission_id=commission_id),
    )
    builder.button(text="❌ لغو", callback_data=AdminAffiliateCallback(action="payouts"))
    builder.adjust(2)
    return builder.as_markup()


def affiliate_orders_keyboard(*, page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="⬅️ صفحه قبل", callback_data=AdminAffiliateCallback(action="orders", page=page - 1))
    if has_next:
        builder.button(text="صفحه بعد ➡️", callback_data=AdminAffiliateCallback(action="orders", page=page + 1))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(1)
    return builder.as_markup()


def attach_orphans_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ اتصال به ریشه", callback_data=AdminAffiliateCallback(action="attach_confirm"))
    builder.button(text="❌ لغو", callback_data=AdminActionCallback(action="affiliate"))
    builder.adjust(2)
    return builder.as_markup()


def pending_payments_keyboard(payments: list[Payment]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for payment in payments:
        tracking_code = payment.order.tracking_code if payment.order else str(payment.id)
        builder.button(
            text=f"✅ تایید {tracking_code}",
            callback_data=AdminPaymentCallback(action="approve", payment_id=payment.id),
        )
        builder.button(
            text=f"❌ رد {tracking_code}",
            callback_data=AdminPaymentCallback(action="reject", payment_id=payment.id),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(*([2] * len(payments)), 1)
    return builder.as_markup()


def wallet_topups_keyboard(transactions: list[WalletTransaction]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for transaction in transactions:
        builder.button(
            text=f"✅ تایید شارژ {transaction.id}",
            callback_data=f"wal_rev:approve:{transaction.id}",
        )
        builder.button(
            text=f"❌ رد شارژ {transaction.id}",
            callback_data=f"wal_rev:reject:{transaction.id}",
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(*([2] * len(transactions)), 1)
    return builder.as_markup()


def wallet_withdrawals_keyboard(withdrawals: list[WalletWithdrawalRequest]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for withdrawal in withdrawals:
        builder.button(
            text=f"🔍 جزئیات برداشت {withdrawal.id}",
            callback_data=AdminWithdrawalCallback(action="detail", withdrawal_id=withdrawal.id),
        )
        if withdrawal.status == "pending":
            builder.button(
                text=f"✅ تایید و پرداخت شد {withdrawal.id}",
                callback_data=AdminWithdrawalCallback(action="pay", withdrawal_id=withdrawal.id),
            )
            builder.button(
                text=f"❌ رد درخواست {withdrawal.id}",
                callback_data=AdminWithdrawalCallback(action="reject", withdrawal_id=withdrawal.id),
            )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="cat_payments"))
    builder.adjust(1)
    return builder.as_markup()


def wallet_withdrawal_review_keyboard(withdrawal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ تایید و پرداخت شد",
        callback_data=AdminWithdrawalCallback(action="pay", withdrawal_id=withdrawal_id),
    )
    builder.button(
        text="❌ رد درخواست",
        callback_data=AdminWithdrawalCallback(action="reject", withdrawal_id=withdrawal_id),
    )
    builder.button(
        text="🔍 جزئیات",
        callback_data=AdminWithdrawalCallback(action="detail", withdrawal_id=withdrawal_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def payment_review_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ تایید پرداخت",
        callback_data=AdminPaymentCallback(action="approve", payment_id=payment_id),
    )
    builder.button(
        text="❌ رد پرداخت",
        callback_data=AdminPaymentCallback(action="reject", payment_id=payment_id),
    )
    builder.adjust(2)
    return builder.as_markup()


def plans_management_keyboard(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ افزودن تعرفه", callback_data=AdminActionCallback(action="add_plan"))
    for plan in plans:
        status = "🟢" if plan.is_active else "🔴"
        toggle_text = "🔴 غیرفعال" if plan.is_active else "🟢 فعال"
        builder.button(
            text=f"⚙️ مدیریت {status} {plan.title}",
            callback_data=AdminPlanCallback(action="detail", plan_id=plan.id),
        )
        builder.button(
            text=toggle_text,
            callback_data=AdminPlanCallback(action="toggle", plan_id=plan.id),
        )
        builder.button(
            text="🗑 حذف",
            callback_data=AdminPlanCallback(action="delete", plan_id=plan.id),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    if plans:
        builder.adjust(1, *([3] * len(plans)), 1)
    else:
        builder.adjust(1)
    return builder.as_markup()


def plan_delete_confirm_keyboard(plan: Plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ بله، حذف شود",
        callback_data=AdminPlanCallback(action="delete_confirm", plan_id=plan.id),
    )
    builder.button(
        text="❌ لغو",
        callback_data=AdminPlanCallback(action="detail", plan_id=plan.id),
    )
    builder.adjust(2)
    return builder.as_markup()


def test_accounts_keyboard(accounts: list[TestAccount]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ افزودن اکانت تست", callback_data=AdminTestAccountCallback(action="add"))
    for account in accounts:
        status = "🟢" if account.is_active else "🔴"
        builder.button(
            text=f"{status} {account.title}",
            callback_data=AdminTestAccountCallback(action="detail", test_account_id=account.id),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def test_account_detail_keyboard(account: TestAccount) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ عنوان", callback_data=AdminTestAccountCallback(action="edit_title", test_account_id=account.id))
    builder.button(text="📝 توضیحات", callback_data=AdminTestAccountCallback(action="edit_desc", test_account_id=account.id))
    builder.button(text="🔗 لینک کانفیگ", callback_data=AdminTestAccountCallback(action="edit_config", test_account_id=account.id))
    builder.button(text="🔗 لینک اشتراک", callback_data=AdminTestAccountCallback(action="edit_sub", test_account_id=account.id))
    builder.button(text="⏳ مدت تست", callback_data=AdminTestAccountCallback(action="edit_duration", test_account_id=account.id))
    builder.button(text="🔢 حداکثر دریافت", callback_data=AdminTestAccountCallback(action="edit_max", test_account_id=account.id))
    toggle_text = "🔴 غیرفعال کردن" if account.is_active else "🟢 فعال کردن"
    builder.button(text=toggle_text, callback_data=AdminTestAccountCallback(action="toggle", test_account_id=account.id))
    builder.button(text="🗑 حذف", callback_data=AdminTestAccountCallback(action="delete", test_account_id=account.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="test_accounts"))
    builder.adjust(1)
    return builder.as_markup()


def users_admin_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔎 جستجوی کاربر", callback_data=AdminUserCallback(action="search"))
    for user in users:
        label = user.telegram_username or user.first_name or str(user.telegram_id)
        builder.button(text=f"👤 {label}", callback_data=AdminUserCallback(action="detail", user_id=user.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def user_detail_keyboard(user: User, *, viewer_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ افزایش موجودی", callback_data=AdminUserCallback(action="add_wallet", user_id=user.id))
    builder.button(text="➖ کاهش موجودی", callback_data=AdminUserCallback(action="sub_wallet", user_id=user.id))
    if user.telegram_id != viewer_id:
        builder.button(text="تغییر وضعیت ادمین", callback_data=AdminUserCallback(action="toggle_admin", user_id=user.id))
    builder.button(text="🧾 سفارش‌های کاربر", callback_data=AdminUserCallback(action="orders", user_id=user.id))
    builder.button(text="🛍 سرویس‌های کاربر", callback_data=AdminUserCallback(action="services", user_id=user.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="users"))
    builder.adjust(1)
    return builder.as_markup()


def services_admin_keyboard(services: list[VPNService]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔎 جستجوی سرویس", callback_data=AdminServiceCallback(action="search"))
    for service in services:
        builder.button(text=f"🛍 {service.username}", callback_data=AdminServiceCallback(action="detail", service_id=service.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
    return builder.as_markup()


def service_detail_keyboard(service: VPNService) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 فعال کردن", callback_data=AdminServiceCallback(action="activate", service_id=service.id))
    builder.button(text="🔴 غیرفعال کردن", callback_data=AdminServiceCallback(action="disable", service_id=service.id))
    builder.button(text="🗓 تمدید دستی", callback_data=AdminServiceCallback(action="extend", service_id=service.id))
    builder.button(text="🔗 ویرایش لینک کانفیگ", callback_data=AdminServiceCallback(action="edit_config", service_id=service.id))
    builder.button(text="🔗 ویرایش لینک اشتراک", callback_data=AdminServiceCallback(action="edit_sub", service_id=service.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="services"))
    builder.adjust(1)
    return builder.as_markup()


def plan_detail_keyboard(plan: Plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ ویرایش عنوان", callback_data=AdminPlanCallback(action="edit_title", plan_id=plan.id))
    builder.button(text="📝 ویرایش توضیحات", callback_data=AdminPlanCallback(action="edit_desc", plan_id=plan.id))
    builder.button(text="🗓 ویرایش مدت", callback_data=AdminPlanCallback(action="edit_duration", plan_id=plan.id))
    builder.button(text="📦 ویرایش حجم", callback_data=AdminPlanCallback(action="edit_volume", plan_id=plan.id))
    builder.button(text="💵 ویرایش قیمت", callback_data=AdminPlanCallback(action="edit_price", plan_id=plan.id))
    builder.button(text="🔢 ویرایش ترتیب نمایش", callback_data=AdminPlanCallback(action="edit_sort", plan_id=plan.id))
    toggle_text = "🔴 غیرفعال کردن" if plan.is_active else "🟢 فعال کردن"
    builder.button(text=toggle_text, callback_data=AdminPlanCallback(action="toggle", plan_id=plan.id))
    builder.button(text="🗑 حذف تعرفه", callback_data=AdminPlanCallback(action="delete", plan_id=plan.id))
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="plans"))
    builder.adjust(1)
    return builder.as_markup()


def add_plan_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ذخیره تعرفه", callback_data=AdminActionCallback(action="save_add_plan"))
    builder.button(text="❌ لغو", callback_data=AdminActionCallback(action="cancel_add_plan"))
    builder.adjust(2)
    return builder.as_markup()


def add_test_account_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ثبت اکانت تست", callback_data=AdminActionCallback(action="save_test_account"))
    builder.button(text="❌ لغو", callback_data=AdminActionCallback(action="cancel_test_account"))
    builder.adjust(2)
    return builder.as_markup()


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ ارسال", callback_data=AdminActionCallback(action="send_broadcast"))
    builder.button(text="❌ لغو", callback_data=AdminActionCallback(action="cancel_broadcast"))
    builder.adjust(2)
    return builder.as_markup()
