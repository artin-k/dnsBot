from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Payment
from app.utils.formatting import format_money


class ReceiptSelectCallback(CallbackData, prefix="rcpt"):
    payment_id: int


def receipt_select_keyboard(payments: list[Payment]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for payment in payments:
        if payment.order:
            label = f"سفارش {payment.order.tracking_code} | {format_money(payment.amount)} تومان"
        else:
            label = f"شارژ کیف پول | {format_money(payment.amount)} تومان"
        builder.button(text=label, callback_data=ReceiptSelectCallback(payment_id=payment.id))
    builder.adjust(1)
    return builder.as_markup()
