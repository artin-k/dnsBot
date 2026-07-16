# bot/routers/start.py
from html import escape
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.users import UsersRepository
from app.services.affiliate_service import AffiliateService
from app.utils.admin_access import is_user_admin
from bot import texts
from bot.keyboards.common import BACK_TO_MAIN_CALLBACK
from bot.keyboards.main_menu import main_menu_keyboard

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return

    users = UsersRepository(session)
    existing_user = await users.get_by_telegram_id(message.from_user.id)
    referral_code = _extract_referral_code(message.text)
    is_root_admin = settings.root_admin_telegram_id == message.from_user.id
    user = await users.create_or_update_from_telegram(
        telegram_id=message.from_user.id,
        telegram_username=message.from_user.username,
        first_name=message.from_user.first_name,
        is_admin=message.from_user.id in settings.admin_ids,
        is_root_admin=is_root_admin,
    )
    await AffiliateService(session, settings).apply_start_referral(
        user=user,
        is_new_user=existing_user is None,
        referral_code=referral_code,
    )
    await session.commit()

    # Dynamically read the custom welcome text you configured inside bot/texts.py
    await message.answer(
        texts.welcome_text(message.from_user.first_name),
        reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)),
    )


@router.message(F.text == texts.BTN_BACK)
async def back_to_main(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await UsersRepository(session).get_by_telegram_id(message.from_user.id) if message.from_user else None
    await message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)))


@router.callback_query(F.data == BACK_TO_MAIN_CALLBACK)
async def back_to_main_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message:
        user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id) if callback.from_user else None
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(is_admin=is_user_admin(user, settings)))


def _extract_referral_code(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    code = parts[1].strip()
    return code or None