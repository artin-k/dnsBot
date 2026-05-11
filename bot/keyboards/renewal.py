from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Plan, VPNService
from app.utils.money import format_toman
from bot import texts

RENEW_BACK_TO_SERVICES = "renew:back_services"
RENEW_BACK_TO_MENU = "renew:back_menu"


class RenewalServiceCallback(CallbackData, prefix="renew_svc"):
    service_id: int


class RenewalPlanCallback(CallbackData, prefix="renew_plan"):
    service_id: int
    plan_id: int


class RenewalConfirmCallback(CallbackData, prefix="renew_ok"):
    service_id: int
    plan_id: int
    discount_roll_id: int = 0


class RenewalDiscountCallback(CallbackData, prefix="renew_disc"):
    service_id: int
    plan_id: int


def renewal_services_keyboard(services: list[VPNService]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        expire_at = service.expire_at.strftime("%Y-%m-%d")
        builder.button(
            text=f"{service.username} | انقضا: {expire_at}",
            callback_data=RenewalServiceCallback(service_id=service.id),
        )
    builder.button(text=texts.BTN_BACK, callback_data=RENEW_BACK_TO_MENU)
    builder.adjust(1)
    return builder.as_markup()


def renewal_plans_keyboard(service_id: int, plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.title} | {format_toman(plan.price)} تومان",
            callback_data=RenewalPlanCallback(service_id=service_id, plan_id=plan.id),
        )
    builder.button(text=texts.BTN_BACK, callback_data=RENEW_BACK_TO_SERVICES)
    builder.adjust(1)
    return builder.as_markup()


def renewal_invoice_keyboard(service_id: int, plan_id: int, discount_roll_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ تایید و پرداخت",
        callback_data=RenewalConfirmCallback(
            service_id=service_id,
            plan_id=plan_id,
            discount_roll_id=discount_roll_id,
        ),
    )
    builder.button(
        text="🎟 استفاده از کد تخفیف",
        callback_data=RenewalDiscountCallback(service_id=service_id, plan_id=plan_id),
    )
    builder.button(text=texts.BTN_BACK, callback_data=RenewalServiceCallback(service_id=service_id))
    builder.adjust(1)
    return builder.as_markup()
