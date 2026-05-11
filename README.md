# telegram-vpn-shop-bot

بات تلگرام فروش اشتراک VPN/config با Python، aiogram 3، SQLAlchemy async و Alembic. اجرای عادی پروژه محلی است و به Docker یا Redis نیاز ندارد؛ `MemoryStorage` همچنان مسیر پیش‌فرض FSM است.

## امکانات

- خرید اشتراک، تمدید سرویس، نمایش سرویس‌های کاربر و تعرفه‌های فعال
- پیگیری سفارش با نمایش خودکار سفارش‌های کاربر و جزئیات هر سفارش
- پرداخت کارت به کارت با ارسال رسید و تایید/رد ادمین
- کیف پول با تایید شماره موبایل تلگرام، درخواست شارژ، تایید ادمین و تاریخچه تراکنش‌ها
- پرداخت سفارش خرید/تمدید از کیف پول در صورت کافی بودن موجودی
- اکانت تست دیتابیسی با محدودیت تعداد دریافت و مدیریت ادمین
- گردونه شانس با Telegram Dice، محدودیت روزانه و کد تخفیف برای عدد ۶
- زیرمجموعه‌گیری و ثبت پاداش کیف پول بعد از خرید موفق
- پنل ادمین برای تعرفه‌ها، پرداخت‌ها، شارژهای کیف پول، اکانت تست، کاربران، سرویس‌ها، تنظیمات، پیام همگانی و وضعیت گردونه

## تنظیم محیط

```powershell
Copy-Item .env.example .env
```

متغیرهای مهم:

```env
BOT_TOKEN=your_bot_token
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_vpn_shop
REDIS_URL=
FSM_STORAGE=memory
ADMIN_IDS=123456789,987654321
SUPPORT_USERNAME=your_support_username
PAYMENT_CARD_NUMBER=0000-0000-0000-0000
PAYMENT_CARD_HOLDER=نام صاحب کارت
ORDER_EXPIRE_MINUTES=15
REFERRAL_REWARD_AMOUNT=0
WALLET_MIN_TOPUP_AMOUNT=50000
WALLET_MAX_TOPUP_AMOUNT=0
DICE_WIN_DISCOUNT_PERCENT=10
DICE_COOLDOWN_HOURS=24
DICE_DISCOUNT_EXPIRE_HOURS=72
```

`ADMIN_IDS` شناسه عددی ادمین‌هاست و به صورت comma-separated خوانده می‌شود. مقدارهای خالی نادیده گرفته می‌شوند. برای اجرای محلی، `FSM_STORAGE=memory` بماند.

## اجرای محلی

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

اجرای migrationها:

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m alembic upgrade head
```

اجرای بات:

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m bot.main
```

## جریان کیف پول

کاربر هنگام ورود به کیف پول باید شماره موبایل خود را با دکمه contact تلگرام ارسال کند. شارژ کیف پول به صورت پرداخت دستی ثبت می‌شود؛ رسید برای همه ادمین‌ها ارسال می‌شود و بعد از تایید، موجودی کاربر و تراکنش کیف پول به‌روزرسانی می‌شود.

## اکانت تست

ادمین از `/admin` می‌تواند اکانت تست اضافه، ویرایش، فعال/غیرفعال یا حذف امن کند. هر کاربر فقط یک بار می‌تواند اکانت تست بگیرد. اگر اکانت فعالی موجود نباشد، پیام «در حال حاضر اکانت تستی موجود نیست.» نمایش داده می‌شود.

## گردونه شانس

کاربر با «🎲 گردونه شانس» تاس تلگرام دریافت می‌کند. اگر عدد ۶ بیاید، کد تخفیف با درصد `DICE_WIN_DISCOUNT_PERCENT` ساخته می‌شود. محدودیت تلاش با `DICE_COOLDOWN_HOURS` و اعتبار کد با `DICE_DISCOUNT_EXPIRE_HOURS` تنظیم می‌شود.

## پنل ادمین

دستور `/admin` برای ادمین‌های `ADMIN_IDS` یا کاربران دارای `is_admin=True` فعال است. پنل شامل مدیریت تعرفه، پرداخت‌های سفارش، شارژ کیف پول، اکانت تست، کاربران، سرویس‌ها، تنظیمات read-only و پیام همگانی است.

## محدودیت‌ها

- اتصال واقعی به پنل VPN هنوز stub است و فقط دیتابیس/لینک placeholder را به‌روزرسانی می‌کند.
- تنظیمات از `.env` خوانده می‌شوند و از داخل ربات فقط نمایش داده می‌شوند.
- پرداخت آنلاین درگاه بانکی پیاده‌سازی نشده و پرداخت‌ها دستی/ادمین‌محور هستند.
