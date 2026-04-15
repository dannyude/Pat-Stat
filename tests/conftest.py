"""Shared pytest fixtures for integration tests."""

import sys
import os

# Override DB URLs before any src.* imports so the SQLAlchemy engine is created
# with localhost (host-machine reachable) instead of the Docker-internal "postgres"
# hostname that only resolves inside the Docker network.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:Dannyude1Ad$@localhost:5432/patstat_db",
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql://postgres:Dannyude1Ad$@localhost:5432/patstat_db",
)

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.core.database import AsyncSessionLocal, engine
from src.main import app

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.database import AsyncSessionLocal, engine


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    from sqlalchemy import text
    from src.core.database import Base

    async with engine.begin() as conn:
        # Drop and recreate the schema to wipe all tables/constraints cleanly.
        # Using CASCADE avoids constraint-ordering issues that arise with
        # Base.metadata.drop_all() when the live DB has different constraint names.
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable slowapi rate limiting for tests so the 100 req/min ceiling
    is never hit during a full test-suite run."""
    from src.core.rate_limit import limiter

    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
def mock_auth_redis_dependencies(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "src.api.v1.auth.store_refresh_token", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.api.v1.auth.revoke_refresh_token", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.api.v1.auth.revoke_all_user_tokens", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.api.v1.auth.is_refresh_token_valid", AsyncMock(return_value=True)
    )

    def _fake_hash_password(plain: str) -> str:
        return f"test-hash::{plain}"

    def _fake_verify_password(plain: str, hashed: str) -> bool:
        return hashed == _fake_hash_password(plain)

    monkeypatch.setattr("src.api.v1.admin.hash_password", _fake_hash_password)
    monkeypatch.setattr("src.api.v1.auth.hash_password", _fake_hash_password)
    monkeypatch.setattr("src.api.v1.auth.verify_password", _fake_verify_password)
    monkeypatch.setattr("src.core.security.hash_password", _fake_hash_password)
    monkeypatch.setattr("src.core.security.verify_password", _fake_verify_password)
    monkeypatch.setattr(
        "src.domains.backoffice.services.hash_password", _fake_hash_password
    )


@pytest_asyncio.fixture(autouse=True)
async def isolate_engine_pool_per_test():
    # asyncpg connections are bound to the event loop that created them.
    # disposing the pool per test prevents cross-loop reuse on Windows/Proactor.
    await engine.dispose()
    yield
    await engine.dispose()


@pytest_asyncio.fixture(name="api_client")
async def fixture_api_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture(name="db_session")
async def fixture_db_session():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
