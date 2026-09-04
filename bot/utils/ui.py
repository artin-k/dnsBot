# bot/utils/ui.py
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup


async def safe_edit_or_reply(
    event: CallbackQuery | Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> Message:
    """Safely edits the existing message or replies if editing is not possible."""
    if isinstance(event, CallbackQuery) and event.message:
        try:
            return await event.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            return await event.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    
    msg = event.message if isinstance(event, CallbackQuery) else event
    return await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)