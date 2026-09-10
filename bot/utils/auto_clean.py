import asyncio
import structlog
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from aiogram import Bot
from datetime import timedelta
from sqlalchemy import update
from app.models import ConfigInventory, Order
from app.services.settings_service import AppSettingsService
from app.config import get_settings
from app.database import async_session_maker
from app.models import VPNService, Order, OrderStatus
from app.services.controld import ControlDService
from app.services.adguard import AdGuardHomeService

logger = structlog.get_logger(__name__)

# --- Your existing function ---
async def schedule_message_deletion(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 7200):
    """Deletes a specific message after a given delay (default 2 hours)."""
    async def _delete_task():
        await asyncio.sleep(delay_seconds)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.debug("failed_to_auto_delete_message", error=str(e))
            
    asyncio.create_task(_delete_task())

# --- NEW: Crash-Proof Automation Loop ---
async def process_expired_services(bot: Bot):
    """Finds expired services, disconnects their IPs, and notifies the user."""
    settings = get_settings()
    controld = ControlDService(settings)
    adguard = AdGuardHomeService(settings)
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        # 1. Find all active services that have passed their expiration date
        stmt = (
            select(VPNService)
            .options(joinedload(VPNService.user))
            .where(
                VPNService.expire_at <= now,
                VPNService.status == "active"
            )
        )
        result = await session.execute(stmt)
        expired_services = result.scalars().all()

        for service in expired_services:
            # 🛑 CRITICAL: Wrap the entire block in a try/except so one bad account doesn't crash the loop
            try:
                ip_to_remove = service.authorized_ip

                # 2. Deauthorize from Control D (Only if both device_id and IP exist)
                if service.controld_device_id and ip_to_remove:
                    try:
                        await controld.deauthorize_ip(service.controld_device_id, ip_to_remove)
                    except Exception as e:
                        logger.error(f"ControlD Deauth failed for service {service.id}: {e}")

                # 3. Deauthorize from AdGuard (Only if IP exists)
                if ip_to_remove:
                    try:
                        await adguard.deauthorize_client_ip(ip_to_remove)
                    except Exception as e:
                        logger.error(f"AdGuard Deauth failed for service {service.id}: {e}")

                # 4. Update the Database Record
                service.status = "expired"
                service.authorized_ip = None
                await session.commit()
                logger.info(f"Service {service.id} successfully expired and cleaned.")

                # 5. Notify the User Safely
                if service.user and service.user.telegram_id:
                    try:
                        if service.is_test_account:
                            msg = "⚠️ <b>پایان اکانت تست</b>\nزمان استفاده از اکانت تست شما به پایان رسید و اتصال دی‌ان‌اس قطع شد."
                        else:
                            msg = f"⚠️ <b>پایان اشتراک</b>\nاشتراک دی‌ان‌اس شما (<code>{service.username or 'بدون نام'}</code>) منقضی شد و دسترسی مسدود گردید. برای تمدید از منوی اصلی اقدام کنید."
                        
                        await bot.send_message(service.user.telegram_id, msg, parse_mode="HTML")
                    except Exception:
                        # User blocked the bot or account deleted; ignore safely
                        pass

            except Exception as e:
                logger.error(f"Failed to process expired service {service.id}: {e}")
                await session.rollback() # Undo any broken database states for this specific user so the loop can move to the next one

async def process_expired_orders(bot: Bot):
    """Cancels pending orders that have exceeded their payment window and frees up inventory."""
    now = datetime.now(timezone.utc)
    
    async with async_session_maker() as session:
        try:
            # 1. Fetch the exact expiration time from the dynamic settings
            app_settings = AppSettingsService(session)
            expire_mins = await app_settings.get_setting("ORDER_EXPIRE_MINUTES", default=15, cast_type=int)
            threshold_time = now - timedelta(minutes=expire_mins)

            # 2. Find pending orders older than the threshold (Load User data for notifications)
            stmt = (
                select(Order)
                .options(joinedload(Order.user))
                .where(
                    Order.status == "pending",
                    Order.created_at <= threshold_time
                )
            )
            result = await session.execute(stmt)
            expired_orders = result.scalars().all()

            if not expired_orders:
                return

            # 3. Process each expired order
            for order in expired_orders:
                order.status = "expired"
                
                # Release any reserved configuration inventory back to the pool
                await session.execute(
                    update(ConfigInventory)
                    .where(ConfigInventory.reserved_by_order_id == order.id)
                    .values(reserved_by_order_id=None)
                )
                order.config_inventory_id = None
                
                # Send Telegram Notification Safely
                if order.user and order.user.telegram_id:
                    try:
                        msg = (
                            f"⚠️ <b>لغو سفارش</b>\n\n"
                            f"سفارش شما با کد پیگیری <code>{order.tracking_code}</code> به دلیل عدم تکمیل پرداخت در زمان مقرر ({expire_mins} دقیقه)، به صورت خودکار لغو شد.\n\n"
                            f"در صورت نیاز می‌توانید مجدداً از منوی اصلی اقدام به خرید نمایید."
                        )
                        await bot.send_message(chat_id=order.user.telegram_id, text=msg, parse_mode="HTML")
                    except Exception:
                        # User blocked the bot or deleted their account; ignore safely
                        pass

            # 4. Save all changes
            await session.commit()
            logger.info(f"Automatically expired {len(expired_orders)} unpaid orders.")
            
        except Exception as e:
            logger.error(f"Failed to process expired orders: {e}")
            await session.rollback()

            
async def auto_cleaner_task(bot: Bot):
    """Infinite loop that runs all cleaning tasks every 60 seconds."""
    logger.info("Auto-cleaner background tasks started.")
    while True:
        # 1. Clean expired subscriptions
        try:
            await process_expired_services(bot)
        except Exception as e:
            logger.error(f"Auto-cleaner services loop crashed: {e}")
            
        # 2. Clean unpaid orders
        try:
            await process_expired_orders(bot)
        except Exception as e:
            logger.error(f"Auto-cleaner orders loop crashed: {e}")
        
        # Wait 1 minute before checking everything again
        await asyncio.sleep(60)