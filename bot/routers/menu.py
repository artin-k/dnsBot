from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.users import UsersRepository
from app.utils.admin_access import is_user_admin
from bot import menu_actions, texts
from bot.keyboards.admin import admin_main_keyboard
from bot.keyboards.main_menu import (
    MENU_ACCOUNT_CALLBACK,
    MENU_BUY_CALLBACK,
    MENU_BUY_RENEW_CALLBACK,
    MENU_DICE_CALLBACK,
    MENU_FEATURES_CALLBACK,
    MENU_MAIN_CALLBACK,
    MENU_ORDERS_CALLBACK,
    MENU_REFERRAL_CALLBACK,
    MENU_RENEW_CALLBACK,
    MENU_TARIFFS_CALLBACK,
    MENU_TEST_CALLBACK,
    MENU_TRACK_CALLBACK,
    MENU_TUTORIALS_CALLBACK,
    MENU_VERIFY_PHONE_CALLBACK,
    MENU_WALLET_CALLBACK,
    main_menu_keyboard,
)
from bot.keyboards.verification import phone_verification_keyboard
from bot.states.wallet import VerificationStates

router = Router(name="menu")

MENU_CALLBACKS = {
    MENU_FEATURES_CALLBACK,
    MENU_BUY_RENEW_CALLBACK,
    MENU_ACCOUNT_CALLBACK,
    MENU_MAIN_CALLBACK,
    MENU_BUY_CALLBACK,
    MENU_RENEW_CALLBACK,
    MENU_TARIFFS_CALLBACK,
    MENU_TRACK_CALLBACK,
    MENU_REFERRAL_CALLBACK,
    MENU_TUTORIALS_CALLBACK,
    MENU_WALLET_CALLBACK,
    MENU_TEST_CALLBACK,
    MENU_DICE_CALLBACK,
    MENU_ORDERS_CALLBACK,
    MENU_VERIFY_PHONE_CALLBACK,
}


@router.message(lambda message: texts.is_main_menu_text(message.text))
async def main_menu_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await handle_main_menu_text(message, state, session, settings)


@router.message(Command("admin"))
async def admin_command(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await state.clear()
    await _show_admin_panel_from_menu(message, session, settings)


async def handle_main_menu_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    if not texts.is_main_menu_text(message.text):
        return False

    await state.clear()
    await route_main_menu_text(message, state, session, settings)
    return True


async def route_main_menu_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    text = (message.text or "").strip()

    if text in {texts.BTN_MAIN_MENU, texts.BTN_BACK}:
        await menu_actions.show_main_menu(message, session, settings)
    elif text in {texts.BTN_BUY_RENEW, "🛒 خرید و تمدید"}:
        await menu_actions.show_buy_renew_menu(message)
    elif text == texts.BTN_FEATURES:
        await menu_actions.show_features_menu(message)
    elif text == texts.BTN_ACCOUNT:
        await menu_actions.show_account_dashboard(message, session)
    elif text == texts.BTN_ADMIN_PANEL:
        await _show_admin_panel_from_menu(message, session, settings)
    elif text == texts.BTN_BUY:
        await menu_actions.show_buy_plans(message, session)
    elif text == texts.BTN_RENEW:
        await menu_actions.show_renewal_disabled(message, session)
    elif text in {texts.BTN_MY_SERVICES, "🛍 سرویس های من"}:
        await menu_actions.show_my_services(message, session)
    elif text in {texts.BTN_TARIFFS, "💰 تعرفه اشتراک ها"}:
        await menu_actions.show_tariffs(message, session)
    elif text == texts.BTN_TRACK_ORDER:
        await menu_actions.show_order_tracking(message, session, settings)
    elif text in texts.REFERRAL_BUTTON_TEXTS:
        await menu_actions.show_referral(message, session, settings)
    elif text == texts.BTN_TUTORIALS:
        await menu_actions.show_tutorials(message)
    elif text == texts.BTN_SUPPORT:
        await menu_actions.show_support(message, session)
    elif text == texts.BTN_WALLET:
        await menu_actions.show_wallet(message, session, state)
    elif text == texts.BTN_TEST_ACCOUNT:
        await menu_actions.show_test_account(message, session)
    elif text == texts.BTN_LUCKY_WHEEL:
        await menu_actions.show_lucky_wheel(message, session, settings)
    else:
        await menu_actions.show_main_menu(message, session, settings)


@router.callback_query(F.data.in_(MENU_CALLBACKS))
async def main_menu_callback(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if callback.message is None:
        return

    action = callback.data
    if action == MENU_MAIN_CALLBACK:
        await state.clear()
        user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id) if callback.from_user else None
        await callback.message.answer(
            texts.MAIN_MENU_TEXT,
            reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)),
        )
    elif action == MENU_BUY_RENEW_CALLBACK:
        await menu_actions.show_buy_renew_menu(callback.message)
    elif action == MENU_FEATURES_CALLBACK:
        await menu_actions.show_features_menu(callback.message)
    elif action == MENU_ACCOUNT_CALLBACK:
        await menu_actions.show_account_dashboard(callback.message, session, settings, telegram_user=callback.from_user)
    elif action == MENU_BUY_CALLBACK:
        await menu_actions.show_buy_plans(callback.message, session)
    elif action == MENU_RENEW_CALLBACK:
        await menu_actions.show_renewal_disabled(callback.message, session)
    elif action == MENU_TARIFFS_CALLBACK:
        await menu_actions.show_tariffs(callback.message, session)
    elif action in {MENU_TRACK_CALLBACK, MENU_ORDERS_CALLBACK}:
        await menu_actions.show_order_tracking(callback.message, session, settings, telegram_user=callback.from_user)
    elif action == MENU_REFERRAL_CALLBACK:
        await state.clear()
        await menu_actions.show_referral(callback.message, session, settings, telegram_user=callback.from_user)
    elif action == MENU_TUTORIALS_CALLBACK:
        await menu_actions.show_tutorials(callback.message)
    elif action == MENU_WALLET_CALLBACK:
        await menu_actions.show_wallet(callback.message, session, state, settings, telegram_user=callback.from_user)
    elif action == MENU_TEST_CALLBACK:
        await menu_actions.show_test_account(callback.message, session, settings, telegram_user=callback.from_user)
    elif action == MENU_DICE_CALLBACK:
        await menu_actions.show_lucky_wheel(callback.message, session, settings, telegram_user=callback.from_user)
    elif action == MENU_VERIFY_PHONE_CALLBACK:
        await state.clear()
        # Directly show the account dashboard instead of asking for phone verification
        await menu_actions.show_account_dashboard(
            callback.message, session, settings, telegram_user=callback.from_user
        )
    
    # elif action == MENU_VERIFY_PHONE_CALLBACK:
    #     await state.clear()
    #     await state.set_state(VerificationStates.waiting_contact)
    #     await state.update_data(next_section="account")
    #     await callback.message.answer(
    #         """برای تایید شماره موبایل، دکمه زیر را بزنید و شماره تلگرام خودتان را ارسال کنید 👇""",
    #         reply_markup=phone_verification_keyboard(),
    #     )


async def _show_admin_panel_from_menu(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    is_admin = (
        message.from_user.id in settings.admin_ids
        or message.from_user.id == settings.root_admin_telegram_id
        or is_user_admin(user, settings)
    )
    if not is_admin:
        await message.answer("⛔ شما دسترسی مدیریت ندارید.")
        return
    await message.answer(texts.ADMIN_PANEL_TEXT, reply_markup=admin_main_keyboard())
