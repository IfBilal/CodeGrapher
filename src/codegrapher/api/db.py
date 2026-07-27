import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

load_dotenv()


class Base(DeclarativeBase):
    pass


def _async_database_url() -> str:
    # DATABASE_URL is postgresql://... (sync-style) in .env; asyncpg needs
    # the postgresql+asyncpg:// scheme instead.
    return os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)


# NullPool, not the default pooled engine: this module is imported both by
# FastAPI (one persistent event loop for the process's lifetime - pooling
# would be fine) and by Celery's tasks.py, where every task invocation calls
# asyncio.run(...), tearing down its event loop when the task finishes. A
# pooled connection from one task's loop is dead by the time the next task
# tries to reuse it ("Future attached to a different loop"). NullPool opens
# a fresh connection per operation and never holds one across calls, so it
# works correctly regardless of which event loop is asking - the standard
# fix for an async engine used across more than one event loop in the same
# process lifetime.
engine = create_async_engine(_async_database_url(), poolclass=NullPool)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
