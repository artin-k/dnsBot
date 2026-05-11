from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.services.payment_service import PaymentService
from app.services.vpn_panel import VPNPanelService
from bot import menu_actions, texts
from bot.keyboards.receipt import ReceiptSelectCallback, receipt_select_keyboard
from bot.notifications import notify_admins_order_payment, notify_admins_wallet_topup
from bot.states.buy import BuyStates

router = Router(name="common")


COMING_SOON_BUTTONS: set[str] = set()


@router.message(F.text.in_(COMING_SOON_BUTTONS))
async def coming_soon(message: Message) -> None:
    await menu_actions.show_coming_soon(message)


@router.message(F.photo)
async def unexpected_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.")
        return

    payments = await PaymentsRepository(session).list_user_pending_without_receipt(user.id)
    if not payments:
        await message.answer("رسیدی برای بررسی پیدا نشد. لطفاً ابتدا یک سفارش یا درخواست شارژ ثبت کنید.")
        return

    receipt_file_id = message.photo[-1].file_id
    if len(payments) == 1:
        await _attach_and_notify(
            message=message,
            session=session,
            settings=settings,
            payment_id=payments[0].id,
            receipt_file_id=receipt_file_id,
        )
        return

    await state.set_state(BuyStates.waiting_receipt_selection)
    await state.update_data(receipt_file_id=receipt_file_id)
    await message.answer(
        "چند پرداخت در انتظار رسید دارید. لطفاً مشخص کنید این رسید مربوط به کدام مورد است:",
        reply_markup=receipt_select_keyboard(payments),
    )


@router.callback_query(ReceiptSelectCallback.filter())
async def select_receipt_payment(
    callback: CallbackQuery,
    callback_data: ReceiptSelectCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    data = await state.get_data()
    receipt_file_id = data.get("receipt_file_id")
    if not receipt_file_id:
        await state.clear()
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    payment = await PaymentsRepository(session).get_with_details(callback_data.payment_id)
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if payment is None or user is None or payment.user_id != user.id:
        await callback.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    await state.clear()
    await callback.answer()
    if callback.message:
        await _attach_and_notify(
            message=callback.message,
            session=session,
            settings=settings,
            payment_id=payment.id,
            receipt_file_id=str(receipt_file_id),
        )


async def _attach_and_notify(
    *,
    message: Message,
    session: AsyncSession,
    settings: Settings,
    payment_id: int,
    receipt_file_id: str,
) -> None:
    payment = await PaymentsRepository(session).get_with_details(payment_id)
    if payment is None:
        await message.answer("پرداخت پیدا نشد.")
        return

    await PaymentService(session, VPNPanelService(), settings).attach_receipt(payment, receipt_file_id)
    if payment.order is not None:
        await message.answer("✅ رسید شما دریافت شد و در انتظار تایید ادمین است.")
        sent_count = await notify_admins_order_payment(
            bot=message.bot,
            session=session,
            settings=settings,
            payment=payment,
            order=payment.order,
            receipt_file_id=receipt_file_id,
        )
    else:
        transaction = await WalletTransactionsRepository(session).get_by_payment_id(payment.id)
        if transaction is None:
            await message.answer("درخواست شارژ کیف پول پیدا نشد.")
            return
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
