import logging

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import Settings
from app.database import async_session_maker

from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.mandatory_channels import DynamicMandatoryJoinMiddleware
from bot.routers import admin, buy, common, errors, mandatory_channels, menu, referral, services, start, support, tariffs, tutorials, tracking, verification, wallet 


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def create_bot(settings: Settings) -> Bot:
    # Simplified direct creation without custom AiohttpSession proxy parameters
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(settings: Settings) -> Dispatcher:
    storage = _create_storage(settings)
    dp = Dispatcher(storage=storage, settings=settings)
    db_middleware = DbSessionMiddleware(async_session_maker)
    dp.update.middleware(db_middleware)

    # Register mandatory channels middleware
    mandatory_join_middleware = DynamicMandatoryJoinMiddleware()
    dp.update.middleware(mandatory_join_middleware)

    dp.include_router(errors.router)
    
    # Admin routers
    dp.include_router(admin.router)
    dp.include_router(mandatory_channels.router)
    
    # User routers
    dp.include_router(referral.router)
    dp.include_router(menu.router)
    dp.include_router(verification.router)
    dp.include_router(start.router)
    dp.include_router(buy.router)
    # dp.include_router(controld_buy.router)
    dp.include_router(services.router)
    dp.include_router(tariffs.router)
    dp.include_router(tracking.router)
    dp.include_router(tutorials.router)
    dp.include_router(wallet.router)
    dp.include_router(support.router)
    dp.include_router(common.router)
    
    return dp


def _create_storage(settings: Settings) -> BaseStorage:
    if settings.fsm_storage == "redis" and settings.redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
        except ImportError as exc:
            raise RuntimeError("Redis FSM storage requires installing the optional redis package.") from exc
        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()