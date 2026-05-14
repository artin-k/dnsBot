# Telegram VPN Shop Bot

## Configuration

The `.env.example` file only contains these technical runtime values:

```env
BOT_TOKEN=your_bot_token
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_vpn_shop
REDIS_URL=
FSM_STORAGE=memory
ADMIN_IDS=123456789,987654321
ALLOW_PLACEHOLDER_CONFIGS=false
CONFIG_LOW_STOCK_THRESHOLD=3
```

Payment, support, wallet, referral reward, and order-expiration settings are stored in the database and managed from the Telegram admin panel.
`ALLOW_PLACEHOLDER_CONFIGS` is disabled by default. Normal purchases require real config inventory.

## Database Migration

After pulling changes, run:

```bash
alembic upgrade head
```

The migrations create the `settings` and `config_inventory` tables. The bot also creates any missing default settings on startup.

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

## Config Inventory

Purchases use real configs imported by admins. Each inventory row belongs to a plan and can be `available`, `reserved`, `sold`, or `disabled`.

Open `/admin` → `📦 فروش و تعرفه‌ها` → `📦 موجودی کانفیگ‌ها`.

Available actions:

- `📊 خلاصه موجودی`: count configs per plan/status.
- `➕ افزودن کانفیگ`: add one config to a selected plan.
- `📥 افزودن گروهی کانفیگ‌ها`: add multiple configs.
- `📋 لیست کانفیگ‌ها`: filter and manage configs.
- `⚠️ تعرفه‌های کم‌موجودی`: show plans at or below `CONFIG_LOW_STOCK_THRESHOLD`.

Bulk format:

```text
vless://aaa
vless://bbb | https://panel.example/sub/bbb
trojan://ccc
```

Each purchase reserves one available config when the order is created. If payment is rejected or the order expires, the config returns to available. When payment is approved, the reserved config becomes sold and the user receives exactly the stored config/subscription links.

Renewal extends the existing service and does not consume new inventory.
