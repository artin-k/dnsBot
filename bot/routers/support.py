from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from bot import menu_actions
from bot import texts

router = Router(name="support")


@router.message(F.text == texts.BTN_SUPPORT)
async def support(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await menu_actions.show_support(message, settings)
