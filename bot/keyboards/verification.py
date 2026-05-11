from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot import texts


def phone_verification_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 ارسال شماره موبایل", request_contact=True)],
            [KeyboardButton(text=texts.BTN_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
