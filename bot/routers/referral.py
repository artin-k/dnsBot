from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from bot import menu_actions
from bot import texts

router = Router(name="referral")


@router.message(F.text == texts.BTN_REFERRAL)
async def referral(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    await state.clear()
    await menu_actions.show_referral(message, session, settings)
