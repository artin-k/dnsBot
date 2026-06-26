# bot/routers/tutorials.py
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.tutorials import TutorialCallback, tutorials_keyboard

router = Router(name="tutorials")


TUTORIAL_TEXTS = {
    "android": """📱 آموزش تنظیم DNS اختصاصی در اندروید (نسخه ۹ به بالا)

۱. وارد «تنظیمات» (Settings) گوشی خود شوید.
۲. به بخش «شبکه و اینترنت» (Network & Internet) یا «اتصالات» (Connections) بروید.
۳. بخش «دی‌ان‌اس خصوصی» (Private DNS) را پیدا کنید (معمولاً در بخش More Connection Settings قرار دارد).
۴. آن را روی حالت «نام میزبان ارائه‌دهنده دی‌ان‌اس خصوصی» (Private DNS provider hostname) قرار دهید.
۵. آدرس DoT اختصاصی خود را که از ربات دریافت کرده‌اید (مانند: xxx.dns.controld.com) وارد کرده و ذخیره (Save) کنید.

⚠️ مهم: در اندرویدهای قدیمی (زیر ۹) باید از برنامه‌هایی مانند DNS Changer یا Intra استفاده کنید و آدرس‌های عددی (Primary/Secondary) را ست کنید.

🔴 توجه حیاتی: پس از تنظیم دی‌ان‌اس، حتماً روی دکمه «ثبت آی‌پی اتوماتیک» در پیام تحویل اشتراک خود کلیک کنید تا دسترسی شما فعال شود.""",
    "iphone": """🍎 آموزش تنظیم DNS اختصاصی در آیفون و آیپد (iOS)

روش اول (پیشنهادی و خودکار):
۱. برای اتصال آسان، پس از تحویل مشخصات، روی لینک پروفایل اختصاصی موبایل خود کلیک کرده و آن را دانلود کنید.
۲. وارد Settings گوشی شده، به بخش Profile Downloaded بروید و روی Install بزنید تا به صورت خودکار ست شود.

روش دوم (تنظیم دستی با آی‌پی):
۱. به Settings -> Wi-Fi بروید.
۲. روی علامت ℹ️ در کنار نام وای‌فای خود بزنید.
۳. به پایین اسکرول کرده و روی Configure DNS بزنید و آن را روی Manual قرار دهید.
۴. آی‌پی‌های Primary و Secondary اختصاصی خود را وارد کرده و Save کنید.

🔴 توجه حیاتی: پس از تنظیم دی‌ان‌اس، حتماً روی دکمه «ثبت آی‌پی اتوماتیک» در پیام تحویل اشتراک خود کلیک کنید تا دسترسی شما فعال شود.""",
    "windows": """💻 آموزش تنظیم DNS اختصاصی در ویندوز (Windows)

۱. وارد منوی Settings (تنظیمات ویندوز) شوید.
۲. به بخش Network & Internet بروید و روی Wi-Fi یا Ethernet کلیک کنید.
۳. روی دکمه Properties (ویژگی‌ها) شبکه فعال خود کلیک کنید.
۴. در بخش DNS server assignment روی دکمه Edit کلیک کنید.
۵. آن را از حالت Automatic به Manual تغییر دهید و کلید IPv4 را روشن کنید.
۶. آدرس‌های Primary DNS و Secondary DNS اختصاصی خود را که ربات به شما داده است وارد کرده و ذخیره (Save) کنید.

🔴 توجه حیاتی: پس از تنظیم دی‌ان‌اس، حتماً بدون فیلترشکن روی دکمه «ثبت آی‌پی اتوماتیک» در پیام تحویل اشتراک خود کلیک کنید تا آی‌پی سیستم شما فعال شود.""",
    "mac": """🖥 آموزش تنظیم DNS اختصاصی در مک‌بوک (macOS)

۱. به منوی Apple  رفته و وارد System Settings (تنظیمات سیستم) شوید.
۲. از منوی سمت چپ روی Network (شبکه) کلیک کنید.
۳. روی نوع اتصال فعال خود (Wi-Fi یا Ethernet) کلیک کرده و دکمه Details را بزنید.
۴. از منوی سمت چپ پنجره باز شده، بخش DNS را انتخاب کنید.
۵. در بخش DNS Servers روی نماد + کلیک کرده و آدرس‌های آی‌پی اختصاصی (Primary و Secondary) را وارد کنید.
۶. روی OK کلیک کرده و سپس Apply کنید.

🔴 توجه حیاتی: پس از تنظیم دی‌ان‌اس، حتماً روی دکمه «ثبت آی‌پی اتوماتیک» در پیام تحویل اشتراک خود کلیک کنید تا دسترسی شما فعال شود.""",
    "links": """🔗 نرم‌افزارهای کاربردی دی‌ان‌اس

اگر ترجیح می‌دهید به جای تنظیمات دستی از نرم‌افزار استفاده کنید، می‌توانید برنامه‌های زیر را دانلود کنید:

📥 اندروید (اندرویدهای قدیمی):
اپلیکیشن رسمی Control D یا اپلیکیشن Intra را از گوگل‌پلی دانلود کنید.

📥 ویندوز / مک / لینوکس:
می‌توانید از نرم‌افزار قدرتمند و متن‌باز DNS Changer استفاده کنید.

⚠️ جهت دانلود برنامه‌ها یا دریافت راهنمایی بیشتر، می‌توانید به پشتیبانی ربات پیام دهید.""",
}


@router.message(F.text == texts.BTN_TUTORIALS)
async def tutorials(message: Message) -> None:
    await message.answer(
        """📚 بخش آموزش

لطفاً سیستم‌عامل یا برنامه مورد نظر خود را انتخاب کنید:""",
        reply_markup=tutorials_keyboard(),
    )


@router.callback_query(TutorialCallback.filter())
async def tutorial_callback(callback: CallbackQuery, callback_data: TutorialCallback) -> None:
    await callback.answer()
    if callback.message is None:
        return

    if callback_data.topic == "back":
        await callback.message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())
        return

    text = TUTORIAL_TEXTS.get(callback_data.topic, texts.COMING_SOON_TEXT)
    try:
        await callback.message.edit_text(text, reply_markup=tutorials_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=tutorials_keyboard())