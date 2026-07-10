import asyncio
from contextlib import suppress

import structlog
from aiogram.exceptions import TelegramNetworkError
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import async_session_maker, engine
from app.services.settings_service import AppSettingsService
from app.services.scheduler import expiration_scheduler_loop
from bot.loader import create_bot, create_dispatcher, setup_logging

logger = structlog.get_logger(__name__)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings)
    
    # Your template automatically attaches routers inside this function!
    dp = create_dispatcher(settings)

    # --- 1. Define database_url ---
    database_url = make_url(settings.database_url)

    # Initialize scheduler_task to prevent NameError in finally block
    scheduler_task = None
    
    logger.info(
        "bot_starting",
        database_host=database_url.host,
        database_port=database_url.port,
        fsm_storage=settings.fsm_storage,
        redis_enabled=settings.fsm_storage == "redis" and bool(settings.redis_url),
        admin_ids_count=len(settings.admin_ids),
        root_admin_configured=settings.root_admin_telegram_id is not None,
    )
    
    if settings.invalid_admin_ids:
        logger.warning("invalid_admin_ids_ignored", values=settings.invalid_admin_ids)
        
    async with async_session_maker() as session:
        await AppSettingsService(session).ensure_defaults()
        await session.commit()

        try:
            from app.services.scheduler import sync_plans_with_controld
            await sync_plans_with_controld(session)
        except Exception as e:
            logger.error("failed_to_sync_controld_profiles", error=str(e))

    try:
        scheduler_task = asyncio.create_task(expiration_scheduler_loop(bot=bot, interval_seconds=3600))
        
        # Start polling loop
        await dp.start_polling(bot)
                
    finally:
        # Graceful cleanup on shutdown
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
                
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
