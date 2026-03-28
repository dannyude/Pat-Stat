from typing import Annotated

from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db as _get_db


async def get_db() -> AsyncSession:
    async for session in _get_db():
        yield session


def pagination_params(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> tuple[int, int]:
    """Reusable pagination dependency: returns (offset, limit)."""
    return (page - 1) * size, size
