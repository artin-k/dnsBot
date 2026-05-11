from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class WalletCallback(CallbackData, prefix="wallet"):
    action: str


class WalletTopupReviewCallback(CallbackData, prefix="wal_rev"):
    action: str
    transaction_id: int


def wallet_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ شارژ کیف پول", callback_data=WalletCallback(action="topup"))
    builder.button(text="📜 تاریخچه تراکنش‌ها", callback_data=WalletCallback(action="history"))
    builder.button(text="↩️ بازگشت", callback_data=WalletCallback(action="back"))
    builder.adjust(1)
    return builder.as_markup()


def wallet_topup_review_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ تایید شارژ کیف پول",
        callback_data=WalletTopupReviewCallback(action="approve", transaction_id=transaction_id),
    )
    builder.button(
        text="❌ رد شارژ کیف پول",
        callback_data=WalletTopupReviewCallback(action="reject", transaction_id=transaction_id),
    )
    builder.adjust(2)
    return builder.as_markup()
