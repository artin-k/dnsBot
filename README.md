# Telegram VPN Shop Bot

## Configuration

The `.env.example` file only contains these technical runtime values:

```env
BOT_TOKEN=your_bot_token
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_vpn_shop
REDIS_URL=
FSM_STORAGE=memory
ADMIN_IDS=123456789,987654321
```

Payment, support, wallet, referral reward, and order-expiration settings are stored in the database and managed from the Telegram admin panel.

## Database Migration

After pulling changes, run:

```bash
alembic upgrade head
```

The migration creates the `settings` table. The bot also creates any missing default settings on startup.

## Admin Settings

After deployment, open the bot and go to `/admin` → `⚙️ تنظیمات` → `⚙️ تنظیمات`.

Configure these values there:

- نام کاربری پشتیبانی
- شماره کارت پرداخت
- نام صاحب کارت
- توضیحات پرداخت
- زمان انقضای سفارش به دقیقه
- مبلغ پاداش زیرمجموعه‌گیری
- حداقل شارژ کیف پول
- حداکثر شارژ کیف پول

Only Telegram IDs listed in `ADMIN_IDS` can access and edit this settings page.
