from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

class TutorialCallback(CallbackData, prefix="edu"):
    topic: str

def tutorials_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 آموزش اندروید", callback_data=TutorialCallback(topic="android"))
    builder.button(text="🍎 آموزش آیفون", callback_data=TutorialCallback(topic="iphone"))
    builder.button(text="💻 آموزش ویندوز", callback_data=TutorialCallback(topic="windows"))
    builder.button(text="🖥 آموزش مک", callback_data=TutorialCallback(topic="mac"))
    builder.button(text="🔗 دریافت لینک برنامه‌ها", callback_data=TutorialCallback(topic="links"))
    builder.button(text="↩️ بازگشت", callback_data=TutorialCallback(topic="back"))
    builder.adjust(1)
    return builder.as_markup()