from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.users import UsersRepository
from bot import menu_actions
from bot.routers.menu import handle_main_menu_text
from bot.states.wallet import VerificationStates

router = Router(name="verification")


@router.message(VerificationStates.waiting_contact, F.contact)
async def receive_contact(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.from_user is None or message.contact is None:
        return
    if message.contact.user_id != message.from_user.id:
        await message.answer("❌ لطفاً شماره موبایل متعلق به حساب تلگرام خودتان را ارسال کنید.")
        return

    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer("ابتدا /start را ارسال کنید.")
        return

    await UsersRepository(session).verify_phone(
        user,
        phone_number=message.contact.phone_number,
        verified_at=datetime.now(timezone.utc),
    )
    await session.commit()
    data = await state.get_data()
    await state.clear()

    await message.answer("✅ شماره موبایل شما با موفقیت تایید شد.")
    if data.get("next_section") == "wallet":
        await menu_actions.show_wallet(message, session)
    else:
        await menu_actions.show_main_menu(message)


@router.message(VerificationStates.waiting_contact, F.text)
async def receive_contact_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if await handle_main_menu_text(message, state, session, settings):
        return
    await message.answer("لطفاً از دکمه «📱 ارسال شماره موبایل» استفاده کنید.")
