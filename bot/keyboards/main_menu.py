from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts

MENU_FEATURES_CALLBACK = "menu:features"
MENU_BUY_RENEW_CALLBACK = "menu:buy_renew"
MENU_ACCOUNT_CALLBACK = "menu:account"
MENU_MAIN_CALLBACK = "menu:main"
MENU_BUY_CALLBACK = "menu:buy"
MENU_RENEW_CALLBACK = "menu:renew"
MENU_TARIFFS_CALLBACK = "menu:tariffs"
MENU_TRACK_CALLBACK = "menu:track"
MENU_REFERRAL_CALLBACK = "menu:referral"
MENU_TUTORIALS_CALLBACK = "menu:tutorials"
MENU_WALLET_CALLBACK = "menu:wallet"
MENU_TEST_CALLBACK = "menu:test"
MENU_DICE_CALLBACK = "menu:dice"
MENU_ORDERS_CALLBACK = "menu:orders"
MENU_VERIFY_PHONE_CALLBACK = "menu:verify_phone"


def main_menu_keyboard(user_or_is_admin: object = False, *, is_admin: bool | None = None) -> ReplyKeyboardMarkup:
    admin = _resolve_admin_flag(user_or_is_admin, is_admin)
    rows = [
        [texts.BTN_BUY_RENEW, texts.BTN_MY_SERVICES],
        [texts.BTN_ACCOUNT, texts.BTN_SUPPORT],
        [texts.BTN_TEST_ACCOUNT, texts.BTN_FEATURES], # <-- Changed this line!
    ]
    if admin:
        rows.append([texts.BTN_ADMIN_PANEL])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=item) for item in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder="یکی از گزینه‌ها را انتخاب کنید",
    )


def compact_main_keyboard(user_or_is_admin: object = False, *, is_admin: bool | None = None) -> ReplyKeyboardMarkup:
    return main_menu_keyboard(user_or_is_admin, is_admin=is_admin)


def _resolve_admin_flag(user_or_is_admin: object, is_admin: bool | None) -> bool:
    if is_admin is not None:
        return bool(is_admin)
    if isinstance(user_or_is_admin, bool):
        return user_or_is_admin
    return bool(
        getattr(user_or_is_admin, "is_admin", False)
        or getattr(user_or_is_admin, "is_root_admin", False)
    )


def buy_renew_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 خرید اشتراک", callback_data=MENU_BUY_CALLBACK)
    builder.button(text="💰 مشاهده تعرفه‌ها", callback_data=MENU_TARIFFS_CALLBACK)
    #builder.button(text="🎁 دریافت اکانت تست", callback_data="get_test_account")
    builder.button(text=texts.BTN_BACK, callback_data=MENU_MAIN_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def features_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 تعرفه اشتراک‌ها", callback_data=MENU_TARIFFS_CALLBACK)
    builder.button(text="📦 پیگیری سفارش", callback_data=MENU_TRACK_CALLBACK)
    builder.button(text="👥 زیرمجموعه‌گیری", callback_data=MENU_REFERRAL_CALLBACK)
    builder.button(text="📚 آموزش", callback_data=MENU_TUTORIALS_CALLBACK)
    builder.button(text="🏦 کیف پول + شارژ", callback_data=MENU_WALLET_CALLBACK)
    builder.button(text="🎲 گردونه شانس", callback_data=MENU_DICE_CALLBACK)
    builder.button(text=texts.BTN_BACK, callback_data=MENU_MAIN_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def account_dashboard_keyboard(*, phone_verified: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏦 کیف پول", callback_data=MENU_WALLET_CALLBACK)
    builder.button(text="📦 سفارش‌های من", callback_data=MENU_ORDERS_CALLBACK)
    builder.button(text="👥 زیرمجموعه‌گیری", callback_data=MENU_REFERRAL_CALLBACK)
    if not phone_verified:
        builder.button(text="📱 تایید شماره موبایل", callback_data=MENU_VERIFY_PHONE_CALLBACK)
    builder.button(text=texts.BTN_BACK, callback_data=MENU_MAIN_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()
