from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.users import UsersRepository
from app.repositories.wallet_transactions import WalletTransactionsRepository
from app.repositories.wallet_withdrawals import WalletWithdrawalsRepository
from app.services.payment_service import PaymentService
from app.services.settings_service import AppSettingsService
from app.services.vpn_panel import VPNPanelService
from app.services.wallet_service import WalletService
from app.services.wallet_withdrawal_service import (
    InsufficientWalletBalanceForWithdrawal,
    WalletWithdrawalService,
)
from app.utils.admin_access import is_user_admin
from app.utils.formatting import (
    format_datetime,
    format_money,
    format_wallet_transaction_status_fa,
    format_wallet_transaction_type_fa,
)
from app.utils.withdrawals import (
    format_withdrawal_destination_fa,
    format_withdrawal_status_fa,
    mask_destination,
    normalize_card_number,
    normalize_sheba_number,
)
from bot import menu_actions, texts
from bot.keyboards.admin import admin_main_keyboard
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.verification import phone_verification_keyboard
from bot.keyboards.wallet import WalletCallback, withdrawal_confirm_keyboard, withdrawal_destination_keyboard
from bot.notifications import notify_admins_wallet_topup, notify_admins_wallet_withdrawal
from bot.routers.menu import handle_main_menu_text
from bot.states.wallet import VerificationStates, WalletStates

router = Router(name="wallet")


@router.message(F.text == texts.BTN_WALLET)
async def wallet(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    await state.clear()
    await menu_actions.show_wallet(message, session, state, settings)


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

    action = callback_data.action
    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        user = await menu_actions._get_or_create_user_from_telegram_user(callback.from_user, session, settings)

    if action == "back":
        await state.clear()
        await callback.message.answer(
            texts.MAIN_MENU_TEXT,
            reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)),
        )
        return

    if action == "withdraw_cancel":
        await state.clear()
        await callback.message.answer("درخواست برداشت لغو شد.", reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)))
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

    if action == "topup":
        min_topup_amount = await AppSettingsService(session).get_wallet_min_topup_amount()
        await state.set_state(WalletStates.waiting_topup_amount)
        await callback.message.answer(
            f"""لطفاً مبلغ شارژ کیف پول را به تومان وارد کنید:

مثال:
100000

حداقل مبلغ شارژ: {format_money(min_topup_amount)} تومان"""
        )
        return

    if action == "history":
        await _show_wallet_history(callback.message, user.id, session)
        return

    if action == "withdrawals":
        await _show_withdrawal_history(callback.message, user.id, session)
        return

    if action == "withdraw":
        await state.set_state(WalletStates.waiting_withdraw_amount)
        await callback.message.answer(
            f"""💸 برداشت از کیف پول

موجودی قابل برداشت شما:
{format_money(user.wallet_balance)} تومان

لطفاً مبلغ برداشت را به تومان وارد کنید:"""
        )
        return

    if action in {"dest_card", "dest_sheba"}:
        data = await state.get_data()
        amount = _parse_positive_int(str(data.get("withdraw_amount") or ""))
        if amount is None:
            await state.clear()
            await callback.message.answer("درخواست برداشت قابل ادامه نیست. لطفاً دوباره تلاش کنید.")
            return
        destination_type = "card" if action == "dest_card" else "sheba"
        await state.update_data(withdraw_destination_type=destination_type)
        await state.set_state(WalletStates.waiting_withdraw_destination_number)
        prompt = (
            "لطفاً شماره کارت مقصد را ارسال کنید:"
            if destination_type == "card"
            else "لطفاً شماره شبا مقصد را ارسال کنید:"
        )
        await callback.message.answer(prompt)
        return

    if action == "withdraw_confirm":
        data = await state.get_data()
        amount = _parse_positive_int(str(data.get("withdraw_amount") or ""))
        destination_type = str(data.get("withdraw_destination_type") or "")
        destination_number = str(data.get("withdraw_destination_number") or "")
        account_holder_name = str(data.get("withdraw_account_holder") or "").strip()
        user_note = data.get("withdraw_user_note")
        if amount is None or destination_type not in {"card", "sheba"} or not destination_number or not account_holder_name:
            await state.clear()
            await callback.message.answer("درخواست برداشت قابل ادامه نیست. لطفاً دوباره تلاش کنید.")
            return
        try:
            withdrawal = await WalletWithdrawalService(session).create_request(
                user_id=user.id,
                amount=amount,
                destination_type=destination_type,
                destination_number=destination_number,
                account_holder_name=account_holder_name,
                user_note=str(user_note).strip() if user_note else None,
            )
        except InsufficientWalletBalanceForWithdrawal:
            await callback.message.answer("❌ موجودی کیف پول شما برای این برداشت کافی نیست.")
            return

        await state.clear()
        await callback.message.answer(
            f"""✅ درخواست برداشت شما ثبت شد و در انتظار بررسی مدیریت است.

💵 مبلغ: {format_money(amount)} تومان
🧾 کد درخواست: {withdrawal.id}""",
            reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)),
        )
        withdrawal_with_details = await WalletWithdrawalsRepository(session).get_with_details(withdrawal.id)
        if withdrawal_with_details is not None:
            await notify_admins_wallet_withdrawal(
                bot=callback.bot,
                session=session,
                settings=settings,
                withdrawal=withdrawal_with_details,
            )
        return


@router.message(WalletStates.waiting_topup_amount, F.text)
async def receive_topup_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    if message.from_user is None:
        return

    amount = _parse_positive_int(message.text)
    if amount is None:
        await message.answer("لطفاً یک مبلغ صحیح و مثبت به تومان وارد کنید.")
        return
    app_settings = AppSettingsService(session)
    min_topup_amount = await app_settings.get_wallet_min_topup_amount()
    max_topup_amount = await app_settings.get_wallet_max_topup_amount()
    if amount < min_topup_amount:
        await message.answer(f"حداقل مبلغ شارژ کیف پول {format_money(min_topup_amount)} تومان است.")
        return
    if max_topup_amount > 0 and amount > max_topup_amount:
        await message.answer(f"حداکثر مبلغ شارژ کیف پول {format_money(max_topup_amount)} تومان است.")
        return

    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    if not user.is_phone_verified:
        await state.clear()
        await menu_actions.show_wallet(message, session, state, settings)
        return

    payment, transaction = await WalletService(session).create_topup_request(user_id=user.id, amount=amount)
    card_number = await app_settings.get_payment_card_number()
    card_holder = await app_settings.get_payment_card_holder()
    payment_description = await app_settings.get_payment_description()
    description_text = f"\nتوضیحات پرداخت:\n{escape(payment_description)}\n" if payment_description else ""
    await state.set_state(WalletStates.waiting_topup_receipt)
    await state.update_data(payment_id=payment.id, transaction_id=transaction.id)
    await message.answer(
        f"""💳 شارژ کیف پول

مبلغ قابل پرداخت:
{format_money(amount)} تومان

شماره کارت:
{escape(card_number) or "ثبت نشده"}

به نام:
{escape(card_holder) or "ثبت نشده"}
{description_text}

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
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    await message.answer("لطفاً تصویر رسید شارژ کیف پول را ارسال کنید.")


@router.message(WalletStates.waiting_withdraw_amount, F.text)
async def receive_withdraw_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    if message.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    if not user.is_phone_verified:
        await state.clear()
        await menu_actions.show_wallet(message, session, state, settings)
        return

    amount = _parse_positive_int(message.text)
    if amount is None:
        await message.answer("لطفاً یک مبلغ صحیح و مثبت به تومان وارد کنید.")
        return

    app_settings = AppSettingsService(session)
    min_amount = await app_settings.get_wallet_min_withdraw_amount()
    max_amount = await app_settings.get_wallet_max_withdraw_amount()
    if amount > user.wallet_balance:
        await message.answer("❌ موجودی کیف پول شما برای این برداشت کافی نیست.")
        return
    if amount < min_amount:
        await message.answer(f"حداقل مبلغ برداشت {format_money(min_amount)} تومان است.")
        return
    if max_amount > 0 and amount > max_amount:
        await message.answer(f"حداکثر مبلغ برداشت {format_money(max_amount)} تومان است.")
        return

    await state.update_data(withdraw_amount=amount)
    await message.answer("لطفاً روش دریافت وجه را انتخاب کنید:", reply_markup=withdrawal_destination_keyboard())


@router.message(WalletStates.waiting_withdraw_destination_number, F.text)
async def receive_withdraw_destination_number(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    data = await state.get_data()
    destination_type = str(data.get("withdraw_destination_type") or "")
    if destination_type == "card":
        destination_number = normalize_card_number(message.text)
        if destination_number is None:
            await message.answer("شماره کارت معتبر نیست. لطفاً شماره کارت ۱۶ رقمی را ارسال کنید.")
            return
    elif destination_type == "sheba":
        destination_number = normalize_sheba_number(message.text)
        if destination_number is None:
            await message.answer("شماره شبا معتبر نیست. لطفاً شماره شبا را با فرمت IRxxxxxxxxxxxxxxxxxxxxxxxx ارسال کنید.")
            return
    else:
        await state.clear()
        await message.answer("درخواست برداشت قابل ادامه نیست. لطفاً دوباره تلاش کنید.")
        return

    await state.update_data(withdraw_destination_number=destination_number)
    await state.set_state(WalletStates.waiting_withdraw_account_holder)
    await message.answer("لطفاً نام صاحب حساب را ارسال کنید:")


@router.message(WalletStates.waiting_withdraw_account_holder, F.text)
async def receive_withdraw_account_holder(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    account_holder_name = (message.text or "").strip()
    if not account_holder_name:
        await message.answer("نام صاحب حساب نمی‌تواند خالی باشد.")
        return
    await state.update_data(withdraw_account_holder=account_holder_name)
    await state.set_state(WalletStates.waiting_withdraw_note)
    await message.answer(
        """اگر توضیحی برای برداشت دارید ارسال کنید.
برای رد شدن از این مرحله، علامت - را ارسال کنید:"""
    )


@router.message(WalletStates.waiting_withdraw_note, F.text)
async def receive_withdraw_note(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await _handle_wallet_menu_interrupt(message, state, session, settings):
        return
    note_text = (message.text or "").strip()
    user_note = None if note_text == "-" else note_text
    await state.update_data(withdraw_user_note=user_note)
    data = await state.get_data()
    amount = int(data["withdraw_amount"])
    destination_type = str(data["withdraw_destination_type"])
    destination_number = str(data["withdraw_destination_number"])
    account_holder_name = str(data["withdraw_account_holder"])

    await message.answer(
        f"""💸 تایید درخواست برداشت

💵 مبلغ: {format_money(amount)} تومان
روش دریافت: {format_withdrawal_destination_fa(destination_type)}
شماره مقصد: {mask_destination(destination_type, destination_number)}
نام صاحب حساب: {escape(account_holder_name)}
توضیحات: {escape(user_note or "-")}

آیا درخواست برداشت ثبت شود؟""",
        reply_markup=withdrawal_confirm_keyboard(),
    )


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


async def _show_withdrawal_history(message: Message, user_id: int, session: AsyncSession) -> None:
    withdrawals = await WalletWithdrawalsRepository(session).list_recent_by_user(user_id, limit=10)
    if not withdrawals:
        await message.answer("شما هنوز درخواست برداشتی ثبت نکرده‌اید.")
        return

    lines = ["📤 درخواست‌های برداشت شما"]
    for index, withdrawal in enumerate(withdrawals, start=1):
        lines.append(
            f"""
{index}. کد: {withdrawal.id}
💵 مبلغ: {format_money(withdrawal.amount)} تومان
وضعیت: {format_withdrawal_status_fa(withdrawal.status)}
تاریخ: {format_datetime(withdrawal.created_at)}"""
        )
    await message.answer("\n".join(lines))


async def _handle_wallet_menu_interrupt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    if await handle_main_menu_text(message, state, session, settings):
        return True
    if (message.text or "").strip().lower() == "/admin":
        await state.clear()
        user = await UsersRepository(session).get_by_telegram_id(message.from_user.id) if message.from_user else None
        if is_user_admin(user, settings):
            await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
        else:
            await message.answer("⛔ شما دسترسی مدیریت ندارید.")
        return True
    if not texts.is_admin_menu_text(message.text):
        return False

    await state.clear()
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id) if message.from_user else None
    if is_user_admin(user, settings):
        await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
    else:
        await message.answer("⛔ شما دسترسی مدیریت ندارید.")
    return True


def _parse_positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    try:
        parsed = int(digits.strip().replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed > 0 else None
