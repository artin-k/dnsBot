from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from bot import menu_actions, texts

router = Router(name="menu")


@router.message(lambda message: texts.is_main_menu_text(message.text))
async def main_menu_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await handle_main_menu_text(message, state, session, settings)


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
        await menu_actions.show_main_menu(message)
    elif text == texts.BTN_BUY:
        await menu_actions.show_buy_plans(message, session)
    elif text == texts.BTN_RENEW:
        await menu_actions.show_renewal_services(message, session)
    elif text == texts.BTN_MY_SERVICES:
        await menu_actions.show_my_services(message, session)
    elif text == texts.BTN_TARIFFS:
        await menu_actions.show_tariffs(message, session)
    elif text == texts.BTN_TRACK_ORDER:
        await menu_actions.show_order_tracking(message, session, settings)
    elif text == texts.BTN_REFERRAL:
        await menu_actions.show_referral(message, session, settings)
    elif text == texts.BTN_TUTORIALS:
        await menu_actions.show_tutorials(message)
    elif text == texts.BTN_SUPPORT:
        await menu_actions.show_support(message, settings)
    elif text == texts.BTN_WALLET:
        await menu_actions.show_wallet(message, session, state)
    elif text == texts.BTN_TEST_ACCOUNT:
        await menu_actions.show_test_account(message, session)
    elif text == texts.BTN_LUCKY_WHEEL:
        await menu_actions.show_lucky_wheel(message, session, settings)
    else:
        await menu_actions.show_main_menu(message)
