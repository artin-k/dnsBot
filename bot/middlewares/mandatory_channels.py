from __future__ import annotations

import structlog
from aiogram import BaseMiddleware, Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject, User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.mandatory_channels import MandatoryChannelsRepository

logger = structlog.get_logger(__name__)


class DynamicMandatoryJoinMiddleware(BaseMiddleware):
    """
    Middleware that checks if users are members of all mandatory channels.
    If a user is missing any channels, blocks the handler and displays join buttons.
    
    Note: This middleware only checks in private chats and skips service messages.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        # Only check for messages
        if not isinstance(event, Message):
            return await handler(event, data)

        # Skip service messages and non-text messages
        if not event.text or event.message_auto_delete_timer_changed or event.group_chat_created:
            return await handler(event, data)

        # Extract user and session from data
        user: TelegramUser | None = event.from_user
        session: AsyncSession | None = data.get("session")
        bot: Bot | None = data.get("bot")

        if not user or not session or not bot:
            return await handler(event, data)

        # Only check in private chats
        if event.chat.type != "private":
            return await handler(event, data)

        try:
            # Check mandatory channels
            repo = MandatoryChannelsRepository(session)
            channels = await repo.get_all_active()

            if not channels:
                # No mandatory channels configured
                return await handler(event, data)

            # Check membership for each mandatory channel
            unjoined_channels = []
            for channel in channels:
                try:
                    member = await bot.get_chat_member(
                        chat_id=channel.channel_id,
                        user_id=user.id,
                    )
                    # Check if user is actually a member
                    if member.status not in ("member", "administrator", "creator"):
                        unjoined_channels.append(channel)
                except Exception as e:
                    logger.warning(
                        "failed_to_check_channel_membership",
                        channel_id=channel.channel_id,
                        user_id=user.id,
                        error=str(e),
                    )
                    # If we can't verify, assume they're not a member (safer)
                    unjoined_channels.append(channel)

            if unjoined_channels:
                # User is missing at least one channel
                await self._send_mandatory_channels_message(event, bot, unjoined_channels)
                return  # Block the handler chain

        except Exception as e:
            # Log the error but DON'T block the user - let them through
            # This prevents outages if the mandatory channels system fails
            logger.error(
                "mandatory_channels_middleware_error",
                user_id=user.id,
                chat_id=event.chat.id,
                error=str(e),
            )
            # Continue to handler regardless - fail open, not fail closed

        return await handler(event, data)

    async def _send_mandatory_channels_message(self, event: Message, bot: Bot, unjoined_channels: list) -> None:
        """Send a message with buttons for unjoined channels and a refresh button."""

        # Build inline keyboard
        keyboard_buttons: list[list[InlineKeyboardButton]] = []

        # Add a button for each unjoined channel
        for channel in unjoined_channels:
            button = InlineKeyboardButton(
                text=f"📱 {channel.channel_name}",
                url=channel.invite_link,
            )
            keyboard_buttons.append([button])

        # Add refresh button
        refresh_button = InlineKeyboardButton(
            text="🔄 بررسی مجدد",
            callback_data="mandatory_join_check",
        )
        keyboard_buttons.append([refresh_button])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        message_text = (
            "❌ برای استفاده از ربات، باید در کانال‌های زیر عضو شوید:\n\n"
            f"تعداد کانال‌های اجباری: {len(unjoined_channels)}"
        )

        try:
            await bot.send_message(
                chat_id=event.chat.id,
                text=message_text,
                reply_markup=markup,
            )
        except Exception as e:
            logger.error(
                "failed_to_send_mandatory_channels_message",
                chat_id=event.chat.id,
                error=str(e),
            )

