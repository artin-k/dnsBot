import asyncio

import structlog
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import engine
from bot.loader import create_bot, create_dispatcher, setup_logging

logger = structlog.get_logger(__name__)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings)
    dp = create_dispatcher(settings)

    database_url = make_url(settings.database_url)
    logger.info(
        "bot_starting",
        database_host=database_url.host,
        database_port=database_url.port,
        fsm_storage=settings.fsm_storage,
        redis_enabled=settings.fsm_storage == "redis" and bool(settings.redis_url),
        admin_ids_count=len(settings.admin_ids),
    )
    if settings.invalid_admin_ids:
        logger.warning("invalid_admin_ids_ignored", values=settings.invalid_admin_ids)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()
        logger.info("bot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
