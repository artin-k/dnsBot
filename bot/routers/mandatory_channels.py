"""
Admin router for managing mandatory channels.
Handles listing, creating, and deleting mandatory channels.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import Settings
from app.repositories.mandatory_channels import MandatoryChannelsRepository
from app.utils.admin_access import is_admin_identity

logger = structlog.get_logger(__name__)

router = Router()


class MandatoryChannelCreationStates(StatesGroup):
    """FSM states for creating a new mandatory channel."""

    waiting_for_channel_id = State()
    waiting_for_channel_name = State()
    waiting_for_invite_link = State()


async def check_user_mandatory_channels(user_id: int, bot, session: AsyncSession) -> list:
    """
    Check which mandatory channels a user hasn't joined yet.
    Returns list of unjoined channels.
    """
    repo = MandatoryChannelsRepository(session)
    channels = await repo.get_all_active()

    unjoined_channels = []
    for channel in channels:
        try:
            member = await bot.get_chat_member(
                chat_id=channel.channel_id,
                user_id=user_id,
            )
            # Check if user is actually a member
            if member.status not in ("member", "administrator", "creator"):
                unjoined_channels.append(channel)
        except Exception as e:
            logger.warning(
                "failed_to_check_channel_membership",
                channel_id=channel.channel_id,
                user_id=user_id,
                error=str(e),
            )
            # If we can't verify, assume they're not a member (safer)
            unjoined_channels.append(channel)

    return unjoined_channels


@router.callback_query(F.data == "mandatory_join_check")
async def callback_mandatory_join_check(
    query: CallbackQuery,
    bot,
    session: AsyncSession,
) -> None:
    """Refresh check for mandatory channel membership."""

    unjoined_channels = await check_user_mandatory_channels(query.from_user.id, bot, session)

    if unjoined_channels:
        # User still hasn't joined all channels
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
            "❌ هنوز در تمام کانال‌های اجباری عضو نیستید:\n\n"
            f"تعداد باقی مانده: {len(unjoined_channels)}"
        )

        try:
            await query.message.edit_text(
                text=message_text,
                reply_markup=markup,
            )
        except Exception as e:
            logger.warning(
                "failed_to_edit_mandatory_channels_message",
                error=str(e),
            )
            await query.answer("❌ خطا در بروزرسانی", show_alert=True)
        else:
            await query.answer("✅ بررسی شد", show_alert=False)
    else:
        # User has joined all mandatory channels!
        await query.message.delete()
        await query.answer("✅ شما در تمام کانال‌های اجباری عضو هستید!", show_alert=True)


@router.message(Command("admin_channels"))
async def cmd_admin_channels(
    update: Message | CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Display list of all mandatory channels with delete/add buttons."""

    # Check admin access
    if not is_admin_identity(
        telegram_id=update.from_user.id,
        settings=settings,
    ):
        if isinstance(update, CallbackQuery):
            await update.answer("❌ شما دسترسی ادمین ندارید", show_alert=True)
        else:
            await update.answer("❌ شما دسترسی ادمین ندارید")
        return

    repo = MandatoryChannelsRepository(session)
    channels = await repo.get_all_active()

    # Build inline keyboard
    keyboard_buttons: list[list[InlineKeyboardButton]] = []

    if channels:
        for channel in channels:
            button = InlineKeyboardButton(
                text=f"❌ {channel.channel_name}",
                callback_data=f"delete_channel:{channel.id}",
            )
            keyboard_buttons.append([button])

    # Add new channel button
    add_button = InlineKeyboardButton(
        text="➕ افزودن کانال جدید",
        callback_data="add_new_channel",
    )
    keyboard_buttons.append([add_button])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if channels:
        text = "📋 <b>کانال‌های اجباری:</b>\n\n"
        for idx, channel in enumerate(channels, 1):
            text += f"{idx}. {channel.channel_name}\n"
            text += f"   ID: <code>{channel.channel_id}</code>\n"
            text += f"   Link: <a href='{channel.invite_link}'>دیدن</a>\n\n"
    else:
        text = "📋 هیچ کانال اجباری تعریف نشده است.\n\n"

    text += "برای حذف کانال روی دکمه آن کلیک کنید."

    # 🔴 THE FIX: Smoothly handle both clicks and text commands without crashing
    if isinstance(update, CallbackQuery):
        await update.message.edit_text(text, reply_markup=markup)
        await update.answer()
    else:
        await update.answer(text, reply_markup=markup)


@router.callback_query(F.data == "add_new_channel")
async def callback_add_new_channel(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Start the new channel creation process."""

    # Check admin access
    if not is_admin_identity(
        telegram_id=query.from_user.id,
        settings=settings,
    ):
        await query.answer("❌ شما دسترسی ادمین ندارید", show_alert=True)
        return

    await state.set_state(MandatoryChannelCreationStates.waiting_for_channel_id)
    await query.message.answer(
        "🆔 لطفاً شناسه کانال را وارد کنید:\n\n"
        "مثال: <code>-1001234567890</code>\n\n"
        "برای لغو، /cancel را ارسال کنید."
    )
    await query.answer()


@router.message(
    MandatoryChannelCreationStates.waiting_for_channel_id,
    ~Command("cancel"),
)
async def process_channel_id(
    message: Message,
    state: FSMContext,
) -> None:
    """Process the channel ID input."""

    try:
        channel_id = int(message.text)
    except ValueError:
        await message.answer("❌ شناسه کانال باید یک عدد باشد. دوباره سعی کنید:")
        return

    # Store the channel_id in state - we'll check for duplicates during create
    await state.update_data(channel_id=channel_id)
    await state.set_state(MandatoryChannelCreationStates.waiting_for_channel_name)
    await message.answer(
        "📝 لطفاً نام نمایشی کانال را وارد کنید:\n\n"
        "مثال: <code>کانال اصلی</code>\n\n"
        "برای لغو، /cancel را ارسال کنید."
    )


@router.message(
    MandatoryChannelCreationStates.waiting_for_channel_name,
    ~Command("cancel"),
)
async def process_channel_name(
    message: Message,
    state: FSMContext,
) -> None:
    """Process the channel name input."""

    channel_name = message.text.strip()

    if not channel_name or len(channel_name) > 255:
        await message.answer(
            "❌ نام کانال باید بین 1 تا 255 کاراکتر باشد. دوباره سعی کنید:"
        )
        return

    await state.update_data(channel_name=channel_name)
    await state.set_state(MandatoryChannelCreationStates.waiting_for_invite_link)
    await message.answer(
        "🔗 لطفاً لینک دعوت کانال را وارد کنید:\n\n"
        "مثال: <code>https://t.me/mychannel</code>\n\n"
        "برای لغو، /cancel را ارسال کنید."
    )


@router.message(
    MandatoryChannelCreationStates.waiting_for_invite_link,
    ~Command("cancel"),
)
async def process_invite_link(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Process the invite link input and save to database."""

    invite_link = message.text.strip()

    # Basic URL validation
    if not (invite_link.startswith("http://") or invite_link.startswith("https://") or invite_link.startswith("t.me")):
        await message.answer(
            "❌ لینک نامعتبر است. لطفاً لینک معتبری وارد کنید:"
        )
        return

    # Ensure we have https or tg URL
    if invite_link.startswith("t.me"):
        invite_link = f"https://{invite_link}"

    data = await state.get_data()
    channel_id = data.get("channel_id")
    channel_name = data.get("channel_name")

    # Save to database
    repo = MandatoryChannelsRepository(session)
    try:
        await repo.create(
            channel_id=channel_id,
            channel_name=channel_name,
            invite_link=invite_link,
        )
        await session.commit()

        logger.info(
            "mandatory_channel_created",
            channel_id=channel_id,
            channel_name=channel_name,
            admin_id=message.from_user.id,
        )

        await message.answer(
            f"✅ کانال '{channel_name}' با موفقیت اضافه شد!\n\n"
            f"شناسه: <code>{channel_id}</code>"
        )
    except Exception as e:
        logger.error(
            "failed_to_create_mandatory_channel",
            error=str(e),
            channel_id=channel_id,
        )
        await message.answer(
            "❌ خطا در اضافه کردن کانال. لطفاً دوباره سعی کنید."
        )
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("delete_channel:"))
async def callback_delete_channel(
    query: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Delete a mandatory channel."""

    # Check admin access
    if not is_admin_identity(
        telegram_id=query.from_user.id,
        settings=settings,
    ):
        await query.answer("❌ شما دسترسی ادمین ندارید", show_alert=True)
        return

    try:
        channel_db_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("❌ خطا در پردازش درخواست", show_alert=True)
        return

    repo = MandatoryChannelsRepository(session)
    try:
        if await repo.delete_by_id(channel_db_id):
            await session.commit()

            logger.info(
                "mandatory_channel_deleted",
                channel_db_id=channel_db_id,
                admin_id=query.from_user.id,
            )

            await query.answer("✅ کانال با موفقیت حذف شد!", show_alert=True)
            # Refresh the message
            try:
                await query.message.delete()
            except Exception as e:
                logger.warning("failed_to_delete_message", error=str(e))
        else:
            await query.answer("❌ کانال یافت نشد", show_alert=True)
    except Exception as e:
        logger.error(
            "failed_to_delete_mandatory_channel",
            channel_db_id=channel_db_id,
            error=str(e),
        )
        await session.rollback()
        await query.answer("❌ خطا در حذف کانال", show_alert=True)


@router.message(Command("cancel"), StateFilter(MandatoryChannelCreationStates))
async def cmd_cancel_channel_creation(
    message: Message,
    state: FSMContext,
) -> None:
    """Cancel the channel creation process."""

    await state.clear()
    await message.answer("❌ عملیات لغو شد.")
