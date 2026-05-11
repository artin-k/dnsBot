from html import escape
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import DiceRoll, OrderKind, VPNServiceStatus
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.order_service import OrderService
from app.utils.formatting import format_money
from bot import texts
from bot.keyboards.buy import payment_keyboard
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.renewal import (
    RENEW_BACK_TO_MENU,
    RENEW_BACK_TO_SERVICES,
    RenewalConfirmCallback,
    RenewalDiscountCallback,
    RenewalPlanCallback,
    RenewalServiceCallback,
    renewal_invoice_keyboard,
    renewal_plans_keyboard,
    renewal_services_keyboard,
)
from bot.states.buy import BuyStates
from bot.keyboards.services import ServiceActionCallback

router = Router(name="renewal")


@router.message(F.text == texts.BTN_RENEW)
async def renewal_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    await _send_services_for_renewal(message, session, user.id)


@router.callback_query(F.data == RENEW_BACK_TO_MENU)
async def renewal_back_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == RENEW_BACK_TO_SERVICES)
async def renewal_back_to_services(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    await _edit_services_for_renewal(callback, session, user.id)


@router.callback_query(ServiceActionCallback.filter(F.action == "renew"))
async def renew_from_my_services(
    callback: CallbackQuery,
    callback_data: ServiceActionCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_edit_or_answer(callback, "ابتدا /start را ارسال کنید.")
        return
    await _show_plans_for_service(callback, session, user.id, callback_data.service_id)


@router.callback_query(RenewalServiceCallback.filter())
async def select_service(
    callback: CallbackQuery,
    callback_data: RenewalServiceCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_edit_or_answer(callback, "ابتدا /start را ارسال کنید.")
        return
    await _show_plans_for_service(callback, session, user.id, callback_data.service_id)


@router.callback_query(RenewalPlanCallback.filter())
async def select_renewal_plan(
    callback: CallbackQuery,
    callback_data: RenewalPlanCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_edit_or_answer(callback, "ابتدا /start را ارسال کنید.")
        return

    service = await ServicesRepository(session).get_user_service(callback_data.service_id, user.id)
    plan = await PlansRepository(session).get(callback_data.plan_id)
    if service is None or service.status != VPNServiceStatus.ACTIVE.value:
        await _safe_edit_or_answer(callback, "این سرویس برای تمدید در دسترس نیست.")
        return
    if plan is None or not plan.is_active:
        await _safe_edit_or_answer(callback, "این تعرفه در دسترس نیست.")
        return

    text = f"""♻️ پیش‌فاکتور تمدید سرویس

👤 نام کاربری سرویس: {escape(service.username)}
⚡ پلن تمدید: {escape(plan.title)}
📦 حجم افزوده: {plan.volume_gb} گیگ
🗓 مدت افزوده: {plan.duration_days} روز
💵 مبلغ: {format_money(plan.price)} تومان

آیا تایید می‌کنید؟"""
    await _safe_edit_or_answer(
        callback,
        text,
        reply_markup=renewal_invoice_keyboard(service.id, plan.id),
    )


@router.callback_query(RenewalDiscountCallback.filter())
async def ask_renewal_discount_code(
    callback: CallbackQuery,
    callback_data: RenewalDiscountCallback,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.set_state(BuyStates.waiting_discount_code)
    await state.update_data(
        flow="renewal",
        service_id=callback_data.service_id,
        plan_id=callback_data.plan_id,
    )
    if callback.message:
        await callback.message.answer("🎟 کد تخفیف خود را ارسال کنید:")


@router.callback_query(RenewalConfirmCallback.filter())
async def confirm_renewal(
    callback: CallbackQuery,
    callback_data: RenewalConfirmCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    service = await ServicesRepository(session).get_user_service(callback_data.service_id, user.id) if user else None
    plan = await PlansRepository(session).get(callback_data.plan_id)

    if user is None or service is None or service.status != VPNServiceStatus.ACTIVE.value:
        await _safe_edit_or_answer(callback, "این سرویس برای تمدید در دسترس نیست.")
        return
    if plan is None or not plan.is_active:
        await _safe_edit_or_answer(callback, "این تعرفه در دسترس نیست.")
        return

    discount = await _get_discount_from_callback(callback, session, callback_data.discount_roll_id, plan.price)
    if callback_data.discount_roll_id and discount is None:
        await _safe_edit_or_answer(callback, "کد تخفیف معتبر نیست یا منقضی شده است.")
        return

    order, _payment = await OrderService(session, settings).create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=service.username,
        order_kind=OrderKind.RENEWAL.value,
        service_id=service.id,
        discount_code=discount.discount_code if discount else None,
        discount_percent=discount.discount_percent if discount else 0,
        discount_amount=_discount_amount(plan.price, discount) if discount else 0,
    )

    text = f"""✅ سفارش تمدید شما ایجاد شد

🛒 کد پیگیری: {order.tracking_code}
👤 سرویس: {escape(service.username)}
⚡ پلن تمدید: {escape(plan.title)}
💵 مبلغ: {format_money(order.amount)} تومان

برای ادامه، پرداخت دستی را انجام دهید و تصویر رسید را ارسال کنید."""
    await _safe_edit_or_answer(callback, text, reply_markup=payment_keyboard(order.id))


async def _send_services_for_renewal(message: Message, session: AsyncSession, user_id: int) -> None:
    services = await ServicesRepository(session).list_active_by_user(user_id)
    if not services:
        await message.answer("شما هنوز سرویس فعالی برای تمدید ندارید.", reply_markup=main_menu_keyboard())
        return
    await message.answer(
        "♻️ لطفاً سرویسی که می‌خواهید تمدید کنید را انتخاب کنید:",
        reply_markup=renewal_services_keyboard(services),
    )


async def _edit_services_for_renewal(callback: CallbackQuery, session: AsyncSession, user_id: int) -> None:
    services = await ServicesRepository(session).list_active_by_user(user_id)
    if not services:
        await _safe_edit_or_answer(callback, "شما هنوز سرویس فعالی برای تمدید ندارید.")
        return
    await _safe_edit_or_answer(
        callback,
        "♻️ لطفاً سرویسی که می‌خواهید تمدید کنید را انتخاب کنید:",
        reply_markup=renewal_services_keyboard(services),
    )


async def _show_plans_for_service(callback: CallbackQuery, session: AsyncSession, user_id: int, service_id: int) -> None:
    service = await ServicesRepository(session).get_user_service(service_id, user_id)
    if service is None or service.status != VPNServiceStatus.ACTIVE.value:
        await _safe_edit_or_answer(callback, "این سرویس برای تمدید در دسترس نیست.")
        return

    plans = await PlansRepository(session).list_active()
    if not plans:
        await _safe_edit_or_answer(callback, "در حال حاضر تعرفه فعالی برای تمدید ثبت نشده است.")
        return

    await _safe_edit_or_answer(
        callback,
        f"♻️ سرویس {escape(service.username)} انتخاب شد.\n\nلطفاً پلن تمدید را انتخاب کنید:",
        reply_markup=renewal_plans_keyboard(service.id, plans),
    )


async def _get_discount_from_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    discount_roll_id: int,
    price: int,
) -> DiceRoll | None:
    if not discount_roll_id or callback.from_user is None:
        return None
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    discount = await DiceRollsRepository(session).get(discount_roll_id)
    now = datetime.now(timezone.utc)
    expires_at = discount.expires_at if discount else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        user is None
        or discount is None
        or discount.user_id != user.id
        or not discount.won
        or discount.used
        or not discount.discount_code
        or (expires_at is not None and expires_at <= now)
        or _discount_amount(price, discount) <= 0
    ):
        return None
    return discount


def _discount_amount(price: int, discount: DiceRoll | None) -> int:
    if discount is None or discount.discount_percent <= 0:
        return 0
    return max(price * discount.discount_percent // 100, 0)


async def _safe_edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            await callback.message.answer(text, reply_markup=reply_markup)
