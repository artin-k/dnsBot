# bot/utils/auto_clean.py
import asyncio
from aiogram import Bot
import structlog

logger = structlog.get_logger(__name__)


async def schedule_message_deletion(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 7200) -> None:
    """
    Schedules a Telegram message to be automatically deleted after delay_seconds.
    Default delay: 7200 seconds (2 hours).
    """
    async def _delete_task():
        await asyncio.sleep(delay_seconds)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.info("auto_deleted_delivery_message", chat_id=chat_id, message_id=message_id)
        except Exception as e:
            # Message might have been deleted manually by user or is older than 48h
            logger.warning("failed_to_auto_delete_message", chat_id=chat_id, message_id=message_id, error=str(e))

    asyncio.create_task(_delete_task())


async def schedule_button_removal(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 7200) -> None:
    """
    Removes inline buttons from a message after delay_seconds (keeps text, removes buttons).
    Default delay: 7200 seconds (2 hours).
    """
    async def _remove_buttons_task():
        await asyncio.sleep(delay_seconds)
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
            logger.info("auto_removed_buttons_from_message", chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.warning("failed_to_auto_remove_buttons", chat_id=chat_id, message_id=message_id, error=str(e))

    asyncio.create_task(_remove_buttons_task())