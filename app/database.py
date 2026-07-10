# app/database.py
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy import make_url as sqlalchemy_make_url
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

class Base(AsyncAttrs, DeclarativeBase):
    pass

settings = get_settings()

db_url = sqlalchemy_make_url(settings.database_url)
engine = create_async_engine(
    str(db_url),
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
    connect_args={
        "timeout": 10.0,
        "command_timeout": 30.0,
    }
)

async_session_maker = async_sessionmaker(
    engine,
    expire_on_commit=False,
)