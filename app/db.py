from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=settings.sql_echo)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    # Fetch server-generated/onupdate column values back via RETURNING
    # immediately on flush, instead of marking them expired-to-reload-later
    # — the latter needs an actual async round-trip on next access, which
    # breaks when that access happens from a synchronous context (e.g.
    # Pydantic's model_validate building a response body pre-commit, see
    # app/idempotency.py's stage_response).
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
