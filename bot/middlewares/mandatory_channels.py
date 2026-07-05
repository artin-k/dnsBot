from __future__ import annotations
from typing import Any, Callable, Awaitable
from aiogram import BaseMiddleware
import structlog
from app.config import get_settings
from app.repositories.users import UsersRepository

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject, User as TelegramUser, Update
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.mandatory_channels import MandatoryChannelsRepository
from app.repositories.users import UsersRepository # Added to check admin status
from bot import texts
from bot.routers.mandatory_channels import check_user_mandatory_channels

logger = structlog.get_logger(__name__)


async def _is_admin(telegram_id: int | None, session: AsyncSession, settings) -> bool:
    """Check if the user is an admin to bypass mandatory join checks."""
    if telegram_id is None:
        return False
    if settings and settings.root_admin_telegram_id is not None and telegram_id == settings.root_admin_telegram_id:
        return True
    if settings and telegram_id in settings.admin_ids:
        return True
    user = await UsersRepository(session).get_by_telegram_id(telegram_id)
    return bool(user and user.is_admin)


# bot/middlewares/mandatory_channels.py

class DynamicMandatoryJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # 1. Identify the event type (handling both direct events and the global Update wrapper)
        message = None
        callback_query = None
        
        if isinstance(event, Update):
            if event.message:
                message = event.message
            elif event.callback_query:
                callback_query = event.callback_query
                message = event.callback_query.message
        elif isinstance(event, Message):
            message = event
        elif isinstance(event, CallbackQuery):
            callback_query = event
            message = event.message

        # Safety check: if no message or user is resolved, proceed to handler
        if not message or not message.from_user:
            return await handler(event, data)

        telegram_user = message.from_user
        text = (message.text or "").strip()
        cb_data = (callback_query.data or "").strip() if callback_query else ""

        # 2. Check if the user is an admin (Admins bypass all mandatory checks)
        session = data.get("session")
        settings = get_settings()
        is_admin = False
        
        if telegram_user.id in settings.admin_ids or telegram_user.id == settings.root_admin_telegram_id:
            is_admin = True
        elif session:
            user = await UsersRepository(session).get_by_telegram_id(telegram_user.id)
            if user and user.is_admin:
                is_admin = True

        if is_admin:
            return await handler(event, data)

        # 3. Whitelist check (Bypass checking for /start, main menu, tutorials, support, profile, wheel, tracking, and back commands)
        whitelisted_texts = {
            "/start",
            "/help",
            texts.BTN_BACK,
            texts.BTN_MAIN_MENU,
            texts.BTN_SUPPORT,
            texts.BTN_TUTORIALS,
            texts.BTN_ACCOUNT,
            texts.BTN_LUCKY_WHEEL,
            texts.BTN_TRACK_ORDER,
            "🏠 منوی اصلی",
            "↩️ بازگشت",
            "👤 حساب کاربری",
            "☎️ پشتیبانی",
            "📚 آموزش",
            "🎲 گردونه شانس",
            "📦 پیگیری سفارش"
        }

        whitelisted_callback_prefixes = (
            "menu:",
            "buy_back_to_menu",
            "mandatory_join_check",
            "tutorials:",
            "back_to_main",
        )

        # If it's a whitelisted message text, proceed instantly
        if text in whitelisted_texts:
            return await handler(event, data)

        # If it's a whitelisted callback prefix, proceed instantly
        if cb_data and any(cb_data.startswith(prefix) for prefix in whitelisted_callback_prefixes):
            return await handler(event, data)

        # 4. For all other core actions (e.g., Buy, Test Accounts, Subscriptions), check sponsor channel membership
        bot = data.get("bot")
        if bot and session:
            unjoined_channels = await check_user_mandatory_channels(telegram_user.id, bot, session)
            if unjoined_channels:
                # Build inline keyboard to present unjoined sponsor channels
                keyboard_buttons: list[list[InlineKeyboardButton]] = []
                for channel in unjoined_channels:
                    button = InlineKeyboardButton(
                        text=f"📱 {channel.channel_name}",
                        url=channel.invite_link,
                    )
                    keyboard_buttons.append([button])

                refresh_button = InlineKeyboardButton(
                    text="🔄 بررسی مجدد",
                    callback_data="mandatory_join_check",
                )
                keyboard_buttons.append([refresh_button])

                markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                message_text = (
                    "❌ برای استفاده از خدمات این بخش، ابتدا باید عضو کانال‌های اسپانسر ما شوید:\n\n"
                    "لطفاً پس از عضویت در تمام کانال‌ها، روی دکمه «🔄 بررسی مجدد» کلیک کنید."
                )

                if callback_query:
                    await callback_query.answer("⚠️ ابتدا باید در کانال‌های اسپانسر عضو شوید.", show_alert=True)
                    await callback_query.message.answer(message_text, reply_markup=markup)
                else:
                    await message.answer(message_text, reply_markup=markup)
                return

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