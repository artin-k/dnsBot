from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Plan
from app.utils.money import format_toman
from bot import texts

BUY_BACK_TO_MENU = "buy:back_to_menu"
BUY_BACK_TO_PLANS = "buy:back_to_plans"


class PlanCallback(CallbackData, prefix="plan"):
    plan_id: int


class ConfirmPlanCallback(CallbackData, prefix="buy_confirm"):
    plan_id: int
    discount_roll_id: int = 0


class PaymentCallback(CallbackData, prefix="pay"):
    order_id: int


class WalletPaymentCallback(CallbackData, prefix="wallet_pay"):
    order_id: int


class PurchaseDiscountCallback(CallbackData, prefix="buy_disc"):
    plan_id: int


def plans_keyboard(plans: list[Plan], inventory_counts: dict[int, int] | None = None) -> InlineKeyboardMarkup:
    return plans_inline_keyboard(plans, inventory_counts)


def plans_inline_keyboard(plans: list[Plan], inventory_counts: dict[int, int] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        available_count = inventory_counts.get(plan.id) if inventory_counts is not None else None
        if available_count is not None and available_count <= 0:
            continue
        inventory_text = f" | موجودی: {available_count}" if available_count is not None else ""
        builder.button(
            text=f"{plan.title} | {plan.volume_gb} گیگ | {format_toman(plan.price)} تومان{inventory_text}",
            callback_data=PlanCallback(plan_id=plan.id),
        )
    builder.button(text=texts.BTN_BACK, callback_data=BUY_BACK_TO_MENU)
    builder.adjust(1)
    return builder.as_markup()


def pre_invoice_keyboard(plan_id: int, discount_roll_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ ادامه خرید",
        callback_data=ConfirmPlanCallback(plan_id=plan_id, discount_roll_id=discount_roll_id),
    )
    builder.button(text="🎟 استفاده از کد تخفیف", callback_data=PurchaseDiscountCallback(plan_id=plan_id))
    builder.button(text=texts.BTN_BACK, callback_data=BUY_BACK_TO_PLANS)
    builder.adjust(1)
    return builder.as_markup()


def payment_keyboard(order_id: int, show_wallet: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 پرداخت کارت به کارت", callback_data=PaymentCallback(order_id=order_id))
    if show_wallet:
        builder.button(text="🏦 پرداخت از کیف پول", callback_data=WalletPaymentCallback(order_id=order_id))
    builder.adjust(1)
    return builder.as_markup()


def insufficient_wallet_keyboard(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ شارژ کیف پول", callback_data="wallet:topup")
    builder.button(text="💳 پرداخت کارت به کارت", callback_data=PaymentCallback(order_id=order_id))
    builder.button(text=texts.BTN_BACK, callback_data=BUY_BACK_TO_MENU)
    builder.adjust(1)
    return builder.as_markup()
