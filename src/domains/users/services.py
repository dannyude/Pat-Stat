"""
Users domain services.
Provides DB abstraction layers for user lookups.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.users.models import User


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Utility to quickly find a user during auth flows or deduplication checks."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()
