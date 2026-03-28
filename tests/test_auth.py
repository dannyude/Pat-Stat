from httpx import AsyncClient
import pytest
from sqlalchemy import delete

from src.domains.users.enums import UserRole
from src.domains.users.models import User
from tests.helpers import unique_email, login_for_token, seed_user

pytestmark = pytest.mark.asyncio


class TestAuth:

    async def test_login(self, api_client: AsyncClient, db_session):
        email = unique_email("login-test")
        # Create admin directly via seed (bootstrap is locked after first test)
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.admin,
            full_name="Login Test User",
        )

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        assert login_resp.status_code == 200
        payload = login_resp.json()
        assert "access_token" in payload
        assert "refresh_token" in payload

    async def test_me_returns_current_user(self, api_client: AsyncClient, db_session):
        email = unique_email("me")
        # Create admin directly via seed
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.super_admin,
            full_name="Current User",
        )

        token = await login_for_token(api_client, email)

        me_resp = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == email

    async def test_change_password(self, api_client: AsyncClient, db_session):
        email = unique_email("pwd")
        # Create admin directly via seed
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.admin,
            full_name="Password User",
        )

        token = await login_for_token(api_client, email)

        change_resp = await api_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "Password123",
                "new_password": "Password456",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert change_resp.status_code == 200

        old_login = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        assert old_login.status_code == 401

        new_login = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password456"},
        )
        assert new_login.status_code == 200

    async def test_public_register_endpoint_removed(self, api_client: AsyncClient):
        """Verify public /register is disabled per invite-only policy."""
        register_resp = await api_client.post(
            "/api/v1/auth/register",
            json={
                "email": unique_email("blocked"),
                "password": "Password123",
                "full_name": "Blocked User",
                "role": "family",
            },
        )
        assert register_resp.status_code == 404
