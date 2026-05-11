from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import DiceRoll, OrderKind, OrderStatus
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.order_service import OrderService
from app.services.payment_service import (
    InsufficientWalletBalanceError,
    PaymentAlreadyProcessedError,
    PaymentApprovalError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.username_validator import validate_username
from app.services.vpn_panel import VPNPanelService
from app.utils.formatting import format_money
from bot import texts
from bot.keyboards.buy import (
    BUY_BACK_TO_MENU,
    BUY_BACK_TO_PLANS,
    ConfirmPlanCallback,
    PurchaseDiscountCallback,
    PaymentCallback,
    PlanCallback,
    WalletPaymentCallback,
    insufficient_wallet_keyboard,
    payment_keyboard,
    plans_keyboard,
    pre_invoice_keyboard,
)
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.renewal import renewal_invoice_keyboard
from bot.notifications import notify_admins_order_payment
from bot.routers.menu import handle_main_menu_text
from bot.states.buy import BuyStates

router = Router(name="buy")


@router.message(F.text == texts.BTN_BUY)
async def show_plans(message: Message, session: AsyncSession) -> None:
    plans = await PlansRepository(session).list_active()
    if not plans:
        await message.answer("در حال حاضر پلن فعالی برای خرید وجود ندارد.", reply_markup=main_menu_keyboard())
        return

    await message.answer(texts.BUY_PLANS_TEXT, reply_markup=plans_keyboard(plans))


@router.callback_query(F.data == BUY_BACK_TO_MENU)
async def buy_back_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == BUY_BACK_TO_PLANS)
async def buy_back_to_plans(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    plans = await PlansRepository(session).list_active()
    if callback.message:
        await callback.message.edit_text(texts.BUY_PLANS_TEXT, reply_markup=plans_keyboard(plans))


@router.callback_query(PlanCallback.filter())
async def show_pre_invoice(
    callback: CallbackQuery,
    callback_data: PlanCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    plan = await PlansRepository(session).get(callback_data.plan_id)
    if plan is None or not plan.is_active:
        await _safe_edit_or_answer(callback, "این پلن در دسترس نیست.")
        return

    user = None
    if callback.from_user:
        user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)

    text = _format_purchase_invoice(plan, user.wallet_balance if user else 0)
    await _safe_edit_or_answer(callback, text, reply_markup=pre_invoice_keyboard(plan.id))


@router.callback_query(PurchaseDiscountCallback.filter())
async def ask_purchase_discount_code(
    callback: CallbackQuery,
    callback_data: PurchaseDiscountCallback,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.set_state(BuyStates.waiting_discount_code)
    await state.update_data(flow="purchase", plan_id=callback_data.plan_id)
    if callback.message:
        await callback.message.answer("🎟 کد تخفیف خود را ارسال کنید:")


@router.callback_query(ConfirmPlanCallback.filter())
async def ask_username(
    callback: CallbackQuery,
    callback_data: ConfirmPlanCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer()
    plan = await PlansRepository(session).get(callback_data.plan_id)
    if plan is None or not plan.is_active:
        await _safe_edit_or_answer(callback, "این پلن در دسترس نیست.")
        return

    discount = await _get_discount_from_callback(callback, session, callback_data.discount_roll_id, plan.price)
    if callback_data.discount_roll_id and discount is None:
        await _safe_edit_or_answer(callback, "کد تخفیف معتبر نیست یا منقضی شده است.")
        return

    await state.set_state(BuyStates.waiting_username)
    await state.update_data(
        plan_id=plan.id,
        discount_code=discount.discount_code if discount else None,
        discount_percent=discount.discount_percent if discount else 0,
        discount_amount=_discount_amount(plan.price, discount) if discount else 0,
    )
    if callback.message:
        await callback.message.answer(texts.USERNAME_PROMPT)


@router.message(BuyStates.waiting_discount_code, F.text)
async def receive_discount_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    if message.from_user is None or message.text is None:
        return

    data = await state.get_data()
    flow = data.get("flow")
    code = message.text.strip().upper()
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    discount = await DiceRollsRepository(session).get_valid_discount(user.id, code, datetime.now(timezone.utc))
    if discount is None:
        await message.answer("❌ کد تخفیف معتبر نیست، استفاده شده یا منقضی شده است.")
        return

    if flow == "purchase":
        plan = await PlansRepository(session).get(int(data.get("plan_id") or 0))
        if plan is None or not plan.is_active:
            await state.clear()
            await message.answer("این پلن در دسترس نیست.", reply_markup=main_menu_keyboard())
            return
        await state.clear()
        await message.answer(
            _format_purchase_invoice(plan, user.wallet_balance, discount),
            reply_markup=pre_invoice_keyboard(plan.id, discount.id),
        )
        return

    if flow == "renewal":
        service = await ServicesRepository(session).get_user_service(
            int(data.get("service_id") or 0),
            user.id,
        )
        plan = await PlansRepository(session).get(int(data.get("plan_id") or 0))
        if service is None or plan is None or not plan.is_active:
            await state.clear()
            await message.answer("تمدید قابل ادامه نیست. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
            return
        await state.clear()
        await message.answer(
            _format_renewal_invoice(service, plan, discount),
            reply_markup=renewal_invoice_keyboard(service.id, plan.id, discount.id),
        )
        return

    await state.clear()
    await message.answer("درخواست تخفیف قابل ادامه نیست. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())


@router.message(BuyStates.waiting_username)
async def receive_username(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    if message.from_user is None or message.text is None:
        return

    is_valid, normalized_or_reason = validate_username(message.text)
    if not is_valid:
        await message.answer(f"❌ {normalized_or_reason}\n\n{texts.USERNAME_PROMPT}")
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    plan = await PlansRepository(session).get(int(plan_id)) if plan_id else None
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)

    if plan is None or not plan.is_active or user is None:
        await state.clear()
        await message.answer("سفارش قابل ادامه نیست. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
        return

    order_service = OrderService(session, settings)
    order, payment = await order_service.create_order_with_payment(
        user=user,
        plan=plan,
        custom_username=normalized_or_reason,
        discount_code=data.get("discount_code"),
        discount_percent=int(data.get("discount_percent") or 0),
        discount_amount=int(data.get("discount_amount") or 0),
    )

    await state.clear()
    await message.answer(
        f"""✅ تراکنش شما ایجاد شد

🛒 کد پیگیری: {order.tracking_code}
💵 مبلغ تراکنش به تومان: {format_money(order.amount)}

💢 لطفاً به این نکات قبل از پرداخت توجه کنید 👇

🔹 تراکنش تا یک ربع اعتبار دارد و پس از آن در صورت پرداخت تایید نخواهد شد.
❌ پس از پرداخت، تایید تراکنش ممکن است 15 دقیقه تا 1 ساعت زمان ببرد.
✅ در صورت مشکل می‌توانید با پشتیبانی در ارتباط باشید.""",
        reply_markup=payment_keyboard(order.id),
    )


@router.callback_query(PaymentCallback.filter())
async def show_payment_info(
    callback: CallbackQuery,
    callback_data: PaymentCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    order = await OrdersRepository(session).get_with_details(callback_data.order_id)
    if order is None or callback.from_user is None or order.user.telegram_id != callback.from_user.id:
        await _safe_edit_or_answer(callback, "این سفارش پیدا نشد.")
        return

    order_service = OrderService(session, settings)
    if await order_service.expire_order_if_unpaid(order):
        await _safe_edit_or_answer(callback, texts.EXPIRED_ORDER_TEXT)
        return

    if order.status not in (OrderStatus.PENDING_PAYMENT.value,):
        await _safe_edit_or_answer(callback, "این سفارش قبلاً پردازش شده است.")
        return

    payment = order.payment
    if payment is None:
        await _safe_edit_or_answer(callback, "پرداخت این سفارش پیدا نشد.")
        return

    await state.set_state(BuyStates.waiting_receipt)
    await state.update_data(order_id=order.id, payment_id=payment.id)
    if callback.message:
        await callback.message.answer(
            f"""💳 پرداخت دستی

مبلغ قابل پرداخت:
{format_money(order.amount)} تومان

شماره کارت:
{escape(settings.payment_card_number) or "ثبت نشده"}

به نام:
{escape(settings.payment_card_holder) or "ثبت نشده"}

بعد از پرداخت، تصویر رسید را همینجا ارسال کنید."""
        )


@router.callback_query(WalletPaymentCallback.filter())
async def pay_from_wallet(
    callback: CallbackQuery,
    callback_data: WalletPaymentCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        await _safe_edit_or_answer(callback, "این سفارش پیدا نشد.")
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    order = await OrdersRepository(session).get_with_details(callback_data.order_id)
    if user is None or order is None or order.user_id != user.id:
        await _safe_edit_or_answer(callback, "این سفارش پیدا نشد.")
        return

    if not user.is_phone_verified:
        await _safe_edit_or_answer(callback, "برای پرداخت از کیف پول، ابتدا از بخش کیف پول شماره موبایل خود را تایید کنید.")
        return

    try:
        result = await PaymentService(session, VPNPanelService(), settings).pay_order_from_wallet(order.id, user.id)
    except InsufficientWalletBalanceError as exc:
        await _safe_edit_or_answer(
            callback,
            f"""❌ موجودی کیف پول شما کافی نیست.

💵 مبلغ سفارش: {format_money(exc.required_amount)} تومان
🏦 موجودی کیف پول: {format_money(exc.wallet_balance)} تومان""",
            reply_markup=insufficient_wallet_keyboard(order.id),
        )
        return
    except PaymentExpiredError:
        await _safe_edit_or_answer(callback, texts.EXPIRED_ORDER_TEXT)
        return
    except PaymentAlreadyProcessedError:
        await _safe_edit_or_answer(callback, "این سفارش قبلاً پردازش شده است.")
        return
    except PaymentApprovalError:
        await _safe_edit_or_answer(callback, "پرداخت از کیف پول قابل انجام نیست.")
        return

    await _safe_edit_or_answer(
        callback,
        _approved_wallet_message(result),
        reply_markup=main_menu_keyboard(),
    )


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    payment_id = data.get("payment_id")
    order = await OrdersRepository(session).get_with_details(int(order_id)) if order_id else None
    payment = await PaymentsRepository(session).get(int(payment_id)) if payment_id else None

    if order is None or payment is None:
        await state.clear()
        await message.answer("پرداخت پیدا نشد. لطفاً دوباره سفارش ثبت کنید.", reply_markup=main_menu_keyboard())
        return

    order_service = OrderService(session, settings)
    if await order_service.expire_order_if_unpaid(order):
        await state.clear()
        await message.answer(texts.EXPIRED_ORDER_TEXT, reply_markup=main_menu_keyboard())
        return

    receipt_file_id = message.photo[-1].file_id
    await PaymentService(session, VPNPanelService(), settings).attach_receipt(payment, receipt_file_id)
    await state.clear()

    await message.answer("✅ رسید شما دریافت شد و در انتظار تایید ادمین است.")
    sent_count = await notify_admins_order_payment(
        bot=message.bot,
        session=session,
        settings=settings,
        payment=payment,
        order=order,
        receipt_file_id=receipt_file_id,
    )
    if sent_count == 0:
        await message.answer("رسید دریافت شد، اما ادمینی برای بررسی تنظیم نشده است. لطفاً با پشتیبانی تماس بگیرید.")


@router.message(BuyStates.waiting_receipt, F.text)
async def receive_non_photo_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    await message.answer("لطفاً تصویر رسید پرداخت را ارسال کنید.")


def _format_purchase_invoice(plan, wallet_balance: int, discount: DiceRoll | None = None) -> str:
    discount_amount = _discount_amount(plan.price, discount)
    final_amount = max(plan.price - discount_amount, 0)
    discount_lines = ""
    if discount and discount_amount:
        discount_lines = f"""
🎟 کد تخفیف: {discount.discount_code}
🎁 تخفیف: {discount.discount_percent}٪ | {format_money(discount_amount)} تومان
💵 مبلغ نهایی: {format_money(final_amount)} تومان"""

    return f"""🧾 پیش‌فاکتور خرید اشتراک

🔐 نام کاربری: در مرحله بعد وارد می‌شود
⚡ نام سرویس: {escape(plan.title)}
📦 حجم: {plan.volume_gb} گیگ
🗓 مدت اعتبار: {plan.duration_days} روز
💵 قیمت: {format_money(plan.price)} تومان{discount_lines}
🏦 موجودی کیف پول شما: {format_money(wallet_balance)} تومان

💰 سفارش شما آماده پرداخت است"""


def _format_renewal_invoice(service, plan, discount: DiceRoll | None = None) -> str:
    discount_amount = _discount_amount(plan.price, discount)
    final_amount = max(plan.price - discount_amount, 0)
    discount_lines = ""
    if discount and discount_amount:
        discount_lines = f"""
🎟 کد تخفیف: {discount.discount_code}
🎁 تخفیف: {discount.discount_percent}٪ | {format_money(discount_amount)} تومان
💵 مبلغ نهایی: {format_money(final_amount)} تومان"""

    return f"""♻️ پیش‌فاکتور تمدید سرویس

👤 نام کاربری سرویس: {escape(service.username)}
⚡ پلن تمدید: {escape(plan.title)}
📦 حجم افزوده: {plan.volume_gb} گیگ
🗓 مدت افزوده: {plan.duration_days} روز
💵 مبلغ: {format_money(plan.price)} تومان{discount_lines}

آیا تایید می‌کنید؟"""


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


def _approved_wallet_message(result) -> str:
    wallet_line = f"\n\n🏦 موجودی کیف پول: {format_money(result.wallet_balance)} تومان" if result.wallet_balance is not None else ""
    if result.order_kind == OrderKind.RENEWAL.value:
        return f"""✅ پرداخت از کیف پول انجام شد و تمدید سرویس شما با موفقیت ثبت شد.

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن تمدید: {escape(result.plan_title)}
📦 حجم افزوده: {result.volume_gb} گیگ
🗓 اعتبار افزوده: {result.duration_days} روز{wallet_line}"""

    return f"""✅ پرداخت از کیف پول انجام شد و سرویس شما ساخته شد.

👤 نام کاربری: {escape(result.service_username)}
⚡ پلن: {escape(result.plan_title)}
📦 حجم: {result.volume_gb} گیگ
🗓 اعتبار: {result.duration_days} روز

🔗 کانفیگ شما:
{escape(result.config_link or "-")}

🔗 لینک اشتراک:
{escape(result.subscription_link or "-")}{wallet_line}"""


async def _safe_edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            await callback.message.answer(text, reply_markup=reply_markup)
