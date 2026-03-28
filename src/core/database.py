from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings

# [DB/Design]: Constraint Naming Convention
# Essential for Alembic auto-migrations. If constraints aren't named explicitly,
# PostgreSQL assigns generic names, meaning Alembic won't know how to drop/alter them.
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    # "ck": "ck_%(table_name)s_%(auto_constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    """
    [Architecture]: The Root ORM Base.
    All SQLAlchemy models (e.g., User, Hospital) inherit from this.
    It binds to the metadata object containing our constraint conventions.
    """
    metadata = metadata


# [Architecture/Async]: Async Engine Configuration
# Uses asyncpg driver to ensure non-blocking IO during DB queries.
# pool_size and max_overflow are explicitly set to handle concurrent WebSocket connections.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.debug,
    pool_pre_ping=True,       # Prevents 'MySQL has gone away' style disconnection errors
    pool_size=10,             # Keep 10 connections warm
    max_overflow=20,          # Allow temporarily bursting up to 30 connections total
)

# [Architecture/Async]: The Async Session Factory
# expire_on_commit=False is crucial for async—preventing detached instance errors 
# when accessing model fields after the commit block safely closes.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db():
    """
    [Logic]: FastAPI Dependency Injection for Database Sessions.
    Guarantees a clean, isolated session per request, rolling back on unhandled errors,
    and reliably closing the connection back to the pool in the `finally` block.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    [Architecture]: Programmatic Table Creation
    Typically unused in production if using Alembic, but helpful for quick bootstrap
    or in-memory sqlite test suites.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["Base", "engine", "AsyncSessionLocal", "get_db", "init_db"]
