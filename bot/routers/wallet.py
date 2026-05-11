from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.users import UsersRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.services.payment_service import PaymentService
from app.services.vpn_panel import VPNPanelService
from app.services.wallet_service import WalletService
from app.utils.formatting import (
    format_datetime,
    format_money,
    format_wallet_transaction_status_fa,
    format_wallet_transaction_type_fa,
)
from bot import menu_actions, texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.verification import phone_verification_keyboard
from bot.keyboards.wallet import WalletCallback
from bot.notifications import notify_admins_wallet_topup
from bot.routers.menu import handle_main_menu_text
from bot.states.wallet import VerificationStates, WalletStates

router = Router(name="wallet")


@router.message(F.text == texts.BTN_WALLET)
async def wallet(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await menu_actions.show_wallet(message, session, state)


@router.callback_query(WalletCallback.filter())
async def wallet_callback(
    callback: CallbackQuery,
    callback_data: WalletCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    if callback_data.action == "back":
        await state.clear()
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())
        return

    if not user.is_phone_verified:
        await state.clear()
        await state.set_state(VerificationStates.waiting_contact)
        await state.update_data(next_section="wallet")
        await callback.message.answer(
            """برای استفاده از این بخش، ابتدا باید شماره موبایل خود را تایید کنید.

لطفاً با دکمه زیر شماره موبایل تلگرام خود را ارسال کنید 👇""",
            reply_markup=phone_verification_keyboard(),
        )
        return

    if callback_data.action == "topup":
        await state.set_state(WalletStates.waiting_topup_amount)
        await callback.message.answer(
            f"""لطفاً مبلغ شارژ کیف پول را به تومان وارد کنید:

مثال:
100000

حداقل مبلغ شارژ: {format_money(settings.wallet_min_topup_amount)} تومان"""
        )
        return

    if callback_data.action == "history":
        await _show_wallet_history(callback.message, user.id, session)
        return


@router.message(WalletStates.waiting_topup_amount, F.text)
async def receive_topup_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    if message.from_user is None:
        return

    amount = _parse_positive_int(message.text)
    if amount is None:
        await message.answer("لطفاً یک مبلغ صحیح و مثبت به تومان وارد کنید.")
        return
    if amount < settings.wallet_min_topup_amount:
        await message.answer(f"حداقل مبلغ شارژ کیف پول {format_money(settings.wallet_min_topup_amount)} تومان است.")
        return
    if settings.wallet_max_topup_amount > 0 and amount > settings.wallet_max_topup_amount:
        await message.answer(f"حداکثر مبلغ شارژ کیف پول {format_money(settings.wallet_max_topup_amount)} تومان است.")
        return

    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    if not user.is_phone_verified:
        await state.clear()
        await menu_actions.show_wallet(message, session, state)
        return

    payment, transaction = await WalletService(session).create_topup_request(user_id=user.id, amount=amount)
    await state.set_state(WalletStates.waiting_topup_receipt)
    await state.update_data(payment_id=payment.id, transaction_id=transaction.id)
    await message.answer(
        f"""💳 شارژ کیف پول

مبلغ قابل پرداخت:
{format_money(amount)} تومان

شماره کارت:
{settings.payment_card_number or "ثبت نشده"}

به نام:
{settings.payment_card_holder or "ثبت نشده"}

بعد از پرداخت، تصویر رسید را همینجا ارسال کنید."""
    )


@router.message(WalletStates.waiting_topup_receipt, F.photo)
async def receive_topup_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    data = await state.get_data()
    transaction_id = data.get("transaction_id")
    transaction = (
        await WalletTransactionsRepository(session).get_with_details(int(transaction_id))
        if transaction_id
        else None
    )
    if transaction is None or transaction.payment is None:
        await state.clear()
        await message.answer("درخواست شارژ پیدا نشد. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
        return

    receipt_file_id = message.photo[-1].file_id
    await PaymentService(session, VPNPanelService(), settings).attach_receipt(transaction.payment, receipt_file_id)
    await state.clear()
    await message.answer("✅ رسید شارژ کیف پول شما دریافت شد و در انتظار تایید مدیریت است.")

    sent_count = await notify_admins_wallet_topup(
        bot=message.bot,
        session=session,
        settings=settings,
        transaction=transaction,
        receipt_file_id=receipt_file_id,
    )
    if sent_count == 0:
        await message.answer("رسید دریافت شد، اما ادمینی برای بررسی تنظیم نشده است. لطفاً با پشتیبانی تماس بگیرید.")


@router.message(WalletStates.waiting_topup_receipt, F.text)
async def receive_topup_receipt_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    await message.answer("لطفاً تصویر رسید شارژ کیف پول را ارسال کنید.")


async def _show_wallet_history(message: Message, user_id: int, session: AsyncSession) -> None:
    transactions = await WalletTransactionsRepository(session).list_recent_by_user(user_id, limit=10)
    if not transactions:
        await message.answer("تراکنشی برای کیف پول شما ثبت نشده است.")
        return

    lines = ["📜 تاریخچه تراکنش‌های کیف پول"]
    for transaction in transactions:
        sign = "+" if transaction.amount > 0 else ""
        lines.append(
            f"""
💵 مبلغ: {sign}{format_money(transaction.amount)} تومان
🔖 نوع: {format_wallet_transaction_type_fa(transaction.type)}
📌 وضعیت: {format_wallet_transaction_status_fa(transaction.status)}
🗓 تاریخ: {format_datetime(transaction.created_at)}
📝 توضیح: {transaction.description or "-"}"""
        )
    await message.answer("\n".join(lines))


def _parse_positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip().replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed > 0 else None
