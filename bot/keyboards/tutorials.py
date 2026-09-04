from aiogram import Router, F
from aiogram.types import CallbackQuery
from bot.keyboards.tutorials import TutorialCallback, tutorials_keyboard

# Adjust the import below based on your project's actual main menu path
from bot.keyboards.main_menu import main_menu_keyboard 

router = Router(name="tutorials")

@router.callback_query(TutorialCallback.filter())
async def handle_tutorial_callbacks(callback: CallbackQuery, callback_data: TutorialCallback):
    await callback.answer()
    topic = callback_data.topic
    
    if topic == "back":
        await callback.message.edit_text(
            "🏠 به منوی اصلی بازگشتید.", 
            reply_markup=main_menu_keyboard()
        )
        return

    # Define your static tutorials here
    tutorials_content = {
        "android": (
            "📱 <b>آموزش اتصال در اندروید:</b>\n\n"
            "1️⃣ ابتدا برنامه v2rayNG را دانلود کنید.\n"
            "2️⃣ لینک کانفیگ خود را کپی کرده و در برنامه جای‌گذاری کنید.\n"
            "3️⃣ دکمه اتصال را بزنید."
        ),
        "iphone": (
            "🍎 <b>آموزش اتصال در آیفون (iOS):</b>\n\n"
            "1️⃣ برنامه V2Box یا FoXray را از اپ استور نصب کنید.\n"
            "2️⃣ روی دکمه + کلیک کرده و لینک اشتراک خود را وارد کنید."
        ),
        "windows": (
            "💻 <b>آموزش اتصال در ویندوز:</b>\n\n"
            "1️⃣ نرم‌افزار v2rayN را دانلود و استخراج کنید.\n"
            "2️⃣ برنامه را اجرا کرده و فایل کانفیگ را وارد کنید."
        ),
        "mac": (
            "🖥 <b>آموزش اتصال در مک (macOS):</b>\n\n"
            "1️⃣ برنامه V2Box یا V2rayU را نصب کنید.\n"
            "2️⃣ لینک اشتراک را در تنظیمات برنامه قرار دهید."
        ),
        "links": (
            "🔗 <b>لینک دانلود برنامه‌های مورد نیاز:</b>\n\n"
            "📥 اندروید (v2rayNG): [لینک گوگل پلی]\n"
            "📥 آیفون (V2Box): [لینک اپ استور]\n"
            "📥 ویندوز (v2rayN): [لینک گیت‌هاب]"
        )
    }

    content = tutorials_content.get(topic, "آموزش این بخش هنوز آماده نیست.")
    
    # Update the message with the tutorial text and keep the keyboard below it
    await callback.message.edit_text(
        text=content,
        reply_markup=tutorials_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )