import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

load_dotenv()


class Base(DeclarativeBase):
    pass


def _async_database_url() -> str:
    # DATABASE_URL is postgresql://... (sync-style) in .env; asyncpg needs
    # the postgresql+asyncpg:// scheme instead.
    return os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)


engine = create_async_engine(_async_database_url())
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
