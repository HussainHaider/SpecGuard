"""Shared API dependencies: database sessions and the job queue."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from specguard.config import Settings, get_settings


def _async_url(url: str) -> str:
    """Convert a psycopg URL to the async driver the API uses."""
    return url.replace("postgresql+psycopg://", "postgresql+asyncpg://")


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """One engine per process."""
    settings: Settings = get_settings()
    return create_async_engine(_async_url(settings.database_url), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """A session per request, committed on success and rolled back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
