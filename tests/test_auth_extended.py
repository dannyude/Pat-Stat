"""Integration tests for auth endpoints not covered by test_auth.py.

Covers: POST /auth/refresh, POST /auth/logout, POST /auth/logout-all,
        PATCH /auth/me, POST /auth/device-token
"""

from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_user, unique_email

pytestmark = pytest.mark.asyncio


class TestTokenRefresh:
    async def test_refresh_issues_new_token_pair(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("refresh-user")
        await seed_user(db_session, email=email, role=UserRole.doctor)

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        assert login_resp.status_code == 200
        tokens = login_resp.json()
        refresh_token = tokens["refresh_token"]

        resp = await api_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # Token rotation: returned refresh token must be a new one
        assert data["refresh_token"] != refresh_token

    async def test_refresh_rejected_when_given_access_token(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("refresh-wrong-type")
        await seed_user(db_session, email=email, role=UserRole.nurse)

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        access_token = login_resp.json()["access_token"]

        # Supplying an access token where a refresh token is expected must fail
        resp = await api_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_returns_success(self, api_client: AsyncClient, db_session):
        email = unique_email("logout-user")
        await seed_user(db_session, email=email, role=UserRole.nurse)

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        tokens = login_resp.json()

        resp = await api_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out"

    async def test_logout_all_returns_success(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("logout-all-user")
        await seed_user(db_session, email=email, role=UserRole.admin)

        access_token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "All sessions revoked"

    async def test_logout_requires_authentication(self, api_client: AsyncClient):
        resp = await api_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "any-token"},
        )
        assert resp.status_code == 401


class TestUpdateMe:
    async def test_update_full_name(self, api_client: AsyncClient, db_session):
        email = unique_email("update-me-name")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Old Name"
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"full_name": "New Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "New Name"

    async def test_update_phone_and_avatar_url(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("update-me-phone")
        await seed_user(db_session, email=email, role=UserRole.nurse)
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={
                "phone": "+2348012345678",
                "avatar_url": "https://cdn.example.com/avatar.png",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "+2348012345678"
        assert data["avatar_url"] == "https://cdn.example.com/avatar.png"

    async def test_partial_update_leaves_other_fields_unchanged(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("update-partial")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Unchanged Name"
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"phone": "08099999999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Unchanged Name"

    async def test_update_me_requires_authentication(self, api_client: AsyncClient):
        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"full_name": "Ghost"},
        )
        assert resp.status_code == 401


class TestDeviceToken:
    async def test_register_device_token(self, api_client: AsyncClient, db_session):
        email = unique_email("device-token")
        await seed_user(db_session, email=email, role=UserRole.doctor)
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/auth/device-token",
            json={"token": "fcm-token-abc123", "device_name": "iPhone 15"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Device token registered"

    async def test_register_device_token_is_idempotent(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("device-token-upsert")
        await seed_user(db_session, email=email, role=UserRole.nurse)
        token = await login_for_token(api_client, email)
        payload = {"token": "fcm-upsert-token-xyz", "device_name": "Pixel 8"}

        first = await api_client.post(
            "/api/v1/auth/device-token",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        second = await api_client.post(
            "/api/v1/auth/device-token",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
