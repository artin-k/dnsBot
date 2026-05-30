from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=20,           # Increased from default 5
    max_overflow=10,        # Allow 10 overflow connections
    pool_recycle=3600,      # Recycle connections every hour
    echo=False,             # Set to True only for debugging
)

async_session_maker = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
