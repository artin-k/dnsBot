from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Payment, Plan, TestAccount, User, VPNService, WalletTransaction


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


def admin_main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 مدیریت تعرفه‌ها", callback_data=AdminActionCallback(action="plans"))
    builder.button(text="🔑 مدیریت اکانت تست", callback_data=AdminActionCallback(action="test_accounts"))
    builder.button(text="💳 پرداخت‌های در انتظار تایید", callback_data=AdminActionCallback(action="payments"))
    builder.button(text="🏦 شارژهای کیف پول", callback_data=AdminActionCallback(action="wallet_topups"))
    builder.button(text="🧾 سفارش‌ها", callback_data=AdminActionCallback(action="orders"))
    builder.button(text="👥 کاربران", callback_data=AdminActionCallback(action="users"))
    builder.button(text="🛍 سرویس‌ها", callback_data=AdminActionCallback(action="services"))
    builder.button(text="🎲 گردونه شانس", callback_data=AdminActionCallback(action="dice"))
    builder.button(text="📢 پیام همگانی", callback_data=AdminActionCallback(action="broadcast"))
    builder.button(text="⚙️ تنظیمات", callback_data=AdminActionCallback(action="settings"))
    builder.button(text="↩️ بازگشت به ربات", callback_data=AdminActionCallback(action="back"))
    builder.adjust(1)
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return admin_main_keyboard()


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
        builder.button(
            text=f"{status} {plan.title}",
            callback_data=AdminPlanCallback(action="detail", plan_id=plan.id),
        )
    builder.button(text="↩️ بازگشت", callback_data=AdminActionCallback(action="panel"))
    builder.adjust(1)
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
