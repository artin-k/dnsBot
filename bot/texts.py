BTN_BUY_RENEW = "🛒 خرید و تمدید"
BTN_BUY = "🔐 خرید اشتراک"
BTN_RENEW = "♻️ تمدید سرویس"
BTN_TEST_ACCOUNT = "🔑 اکانت تست"
BTN_LUCKY_WHEEL = "🎲 گردونه شانس"
BTN_MY_SERVICES = "🛍 سرویس‌های من"
BTN_WALLET = "🏦 کیف پول + شارژ"
BTN_REFERRAL = "👥 زیرمجموعه‌گیری"
BTN_TARIFFS = "💰 تعرفه اشتراک‌ها"
BTN_SUPPORT = "☎️ پشتیبانی"
BTN_TUTORIALS = "📚 آموزش"
BTN_TRACK_ORDER = "📦 پیگیری سفارش"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_FEATURES = "🧭 منوی امکانات"
BTN_ADMIN_PANEL = "🛠 پنل مدیریت"
BTN_BACK = "↩️ بازگشت"
BTN_MAIN_MENU = "🏠 منوی اصلی"

REFERRAL_BUTTON_TEXTS = {
    BTN_REFERRAL,
    "👥 زیر مجموعه گیری",
    "👥 زیرمجموعه گیری",
}

MAIN_MENU_BUTTONS = {
    BTN_BUY_RENEW,
    BTN_BUY,
    BTN_RENEW,
    BTN_TEST_ACCOUNT,
    BTN_LUCKY_WHEEL,
    BTN_MY_SERVICES,
    BTN_WALLET,
    BTN_REFERRAL,
    BTN_TARIFFS,
    BTN_SUPPORT,
    BTN_TUTORIALS,
    BTN_TRACK_ORDER,
    BTN_ACCOUNT,
    BTN_FEATURES,
    BTN_ADMIN_PANEL,
    BTN_BACK,
    BTN_MAIN_MENU,
    "🛍 سرویس های من",
    "💰 تعرفه اشتراک ها",
    *REFERRAL_BUTTON_TEXTS,
}

ADMIN_MENU_BUTTONS = {
    "📦 فروش و تعرفه‌ها",
    "👥 کاربران و زیرمجموعه‌ها",
    "💳 پرداخت‌ها و کیف پول",
    "📣 ارتباطات",
    "📦 فروش و تعرفه ها",
    "👥 کاربران و زیرمجموعه ها",
    "💳 پرداخت ها و کیف پول",
    "📦 مدیریت تعرفه‌ها",
    "📦 موجودی کانفیگ‌ها",
    "📊 خلاصه موجودی",
    "➕ افزودن کانفیگ",
    "📥 افزودن گروهی کانفیگ‌ها",
    "📋 لیست کانفیگ‌ها",
    "🔍 جستجوی کانفیگ",
    "⚠️ تعرفه‌های کم‌موجودی",
    "🔑 مدیریت اکانت تست",
    "💳 پرداخت‌های در انتظار تایید",
    "🏦 شارژهای کیف پول",
    "🧾 سفارش‌ها",
    "👥 کاربران",
    "👥 مدیریت زیرمجموعه‌ها",
    "📊 خلاصه زیرمجموعه‌گیری",
    "🌳 درخت زیرمجموعه‌ها",
    "👤 جستجوی کاربر",
    "💰 گزارش کمیسیون‌ها",
    "🧾 سفارش‌های زیرمجموعه‌ها",
    "🏦 تسویه کمیسیون‌ها",
    "⚙️ تنظیمات زیرمجموعه‌گیری",
    "🧩 اتصال کاربران بدون معرف به ریشه",
    "🛍 سرویس‌ها",
    "🎲 گردونه شانس",
    "📢 پیام همگانی",
    "⚙️ تنظیمات",
    "↩️ بازگشت به ربات",
    "➕ افزودن تعرفه",
    "📋 لیست تعرفه‌ها",
    "✏️ ویرایش تعرفه",
    "🟢 فعال/غیرفعال کردن تعرفه",
    "🗑 حذف تعرفه",
    "↩️ بازگشت به پنل مدیریت",
}

MAIN_MENU_TEXT = "به صفحه اصلی بازگشتید. یکی از گزینه‌های زیر را انتخاب کنید."
COMING_SOON_TEXT = "این بخش به‌زودی فعال می‌شود. تا آن زمان می‌توانید از سایر گزینه‌های منو استفاده کنید."
EXPIRED_ORDER_TEXT = "❌ این تراکنش منقضی شده است. لطفاً سفارش جدید ثبت کنید."

BUY_PLANS_TEXT = "🛍 لطفاً تعرفه مورد نظر خود را برای خرید انتخاب کنید:"

USERNAME_PROMPT = """یک نام کاربری دلخواه برای سرویس ارسال کنید.

نام کاربری باید انگلیسی باشد، با حرف شروع شود و فقط شامل حرف، عدد و آندرلاین باشد.

✅ نمونه صحیح:
ali12 | mahdi | ws1_ksdf

❌ نمونه نادرست:
محسن | ali_ | tele@ | _mahdi"""

ADMIN_PANEL_TEXT = """🛠 پنل مدیریت

لطفاً یک بخش را انتخاب کنید:"""


def welcome_text(first_name: str | None) -> str:
    name = first_name or "کاربر"
    return f"""سلام {name} عزیز 👋

به ربات فروش کانفیگ خوش آمدید 🌟

از اینجا می‌توانید اشتراک بخرید، سرویس‌های خود را مدیریت کنید، کیف پول خود را شارژ کنید و از امکانات زیرمجموعه‌گیری استفاده کنید.

برای شروع، یکی از گزینه‌های زیر را انتخاب کنید 👇"""


def is_main_menu_text(text: str | None) -> bool:
    if text is None:
        return False
    return text.strip() in MAIN_MENU_BUTTONS


def is_admin_menu_text(text: str | None) -> bool:
    if text is None:
        return False
    return text.strip() in ADMIN_MENU_BUTTONS
