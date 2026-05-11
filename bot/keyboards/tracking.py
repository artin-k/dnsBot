from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Order


class OrderDetailCallback(CallbackData, prefix="ord"):
    order_id: int


class OrderSearchCallback(CallbackData, prefix="ord_search"):
    action: str = "code"


def orders_tracking_keyboard(orders: list[Order], *, include_search: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.button(
            text=f"جزئیات سفارش {order.tracking_code}",
            callback_data=OrderDetailCallback(order_id=order.id),
        )
    if include_search:
        builder.button(text="🔎 جستجو با کد پیگیری", callback_data=OrderSearchCallback())
    builder.adjust(1)
    return builder.as_markup()


def order_search_keyboard() -> InlineKeyboardMarkup:
    return orders_tracking_keyboard([], include_search=True)
