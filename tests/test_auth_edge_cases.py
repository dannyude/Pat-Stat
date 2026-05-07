"""Edge-case coverage for the auth endpoints that were previously untested.

Covers:
  • POST /api/v1/auth/logout
  • POST /api/v1/auth/logout-all
  • PATCH /api/v1/auth/me
  • POST /api/v1/auth/device-token

Note: ``conftest.py`` monkey-patches ``store_refresh_token`` /
``revoke_refresh_token`` etc. to AsyncMock(return_value=...). That means
these tests verify the *handler* contract (status code, body shape,
side-effects on the User row in DB) but cannot verify the actual Redis
revocation. The Redis layer is exercised separately in unit tests for
``redis_client.py``.
"""

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from src.core.security import create_refresh_token
from src.domains.users.enums import UserRole
from src.domains.users.models import DeviceToken, User
from tests.helpers import login_for_token, seed_user, unique_email

pytestmark = pytest.mark.asyncio


# ─── /logout & /logout-all ─────────────────────────────────────────────────


class TestLogout:
    async def test_logout_returns_success_message(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("logout-ok")
        await seed_user(db_session, email=email, role=UserRole.doctor)
        token = await login_for_token(api_client, email)

        # The handler decodes the body's refresh_token to extract jti, but our
        # conftest stubs revoke_refresh_token to AsyncMock so any well-formed
        # refresh token works.
        refresh = create_refresh_token("ignored-user-id")

        resp = await api_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out"

    async def test_logout_with_malformed_refresh_token_is_401(
        self, api_client: AsyncClient, db_session
    ):
        """A garbage refresh_token in the body should fail decode → 401."""
        email = unique_email("logout-bad")
        await seed_user(db_session, email=email, role=UserRole.doctor)
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "not.a.real.jwt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    async def test_logout_all_returns_success_message(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("logout-all-ok")
        await seed_user(db_session, email=email, role=UserRole.nurse)
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "All sessions revoked"


# ─── PATCH /me ─────────────────────────────────────────────────────────────


class TestPatchMe:
    async def test_patch_me_updates_full_name(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("patch-me")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Old Name"
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"full_name": "Dr. Updated Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Dr. Updated Name"

    async def test_patch_me_strips_whitespace(
        self, api_client: AsyncClient, db_session
    ):
        """The handler explicitly calls .strip() on full_name — pin that."""
        email = unique_email("patch-me-strip")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Test"
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"full_name": "   Stripped Name   "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Stripped Name"

    async def test_patch_me_empty_phone_becomes_null(
        self, api_client: AsyncClient, db_session
    ):
        """Sending phone="" should normalize to None (per ``or None`` in handler)."""
        email = unique_email("patch-me-phone")
        await seed_user(db_session, email=email, role=UserRole.doctor)
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"phone": "  "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json().get("phone") in (None, "")

    async def test_patch_me_partial_update_preserves_other_fields(
        self, api_client: AsyncClient, db_session
    ):
        """PATCH semantics — fields not in body must be left unchanged."""
        email = unique_email("patch-me-partial")
        await seed_user(
            db_session,
            email=email,
            role=UserRole.doctor,
            full_name="Original Name",
        )
        token = await login_for_token(api_client, email)

        # Send only phone — full_name should NOT be wiped.
        resp = await api_client.patch(
            "/api/v1/auth/me",
            json={"phone": "+2348000000000"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["full_name"] == "Original Name"
        assert body["phone"] == "+2348000000000"


# ─── /device-token ─────────────────────────────────────────────────────────


class TestDeviceToken:
    async def test_register_new_device_token_creates_row(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("dt-new")
        user = await seed_user(db_session, email=email, role=UserRole.family)
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/auth/device-token",
            json={"token": "fake-fcm-aaa-111", "device_name": "iPhone 17"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Device token registered"

        rows = (
            await db_session.execute(
                select(DeviceToken).where(DeviceToken.token == "fake-fcm-aaa-111")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert str(rows[0].user_id) == str(user.id)
        assert rows[0].device_name == "iPhone 17"

    async def test_register_duplicate_token_reassigns_to_new_user(
        self, api_client: AsyncClient, db_session
    ):
        """If the same FCM token is registered by a different user (e.g. the
        device owner logged out and a new user logged in on the same device),
        the row's user_id flips. Pinning this so we don't regress to a
        UniqueViolation 500."""
        # User A registers a token first.
        email_a = unique_email("dt-dup-a")
        user_a = await seed_user(db_session, email=email_a, role=UserRole.family)
        token_a = await login_for_token(api_client, email_a)

        await api_client.post(
            "/api/v1/auth/device-token",
            json={"token": "shared-device-fcm", "device_name": "Shared iPad"},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B registers the same FCM token.
        email_b = unique_email("dt-dup-b")
        user_b = await seed_user(db_session, email=email_b, role=UserRole.family)
        token_b = await login_for_token(api_client, email_b)

        resp = await api_client.post(
            "/api/v1/auth/device-token",
            json={"token": "shared-device-fcm", "device_name": "Shared iPad"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 200

        # Should be exactly ONE row, owned by user B now.
        await db_session.commit()  # ensure we see the API's commit
        rows = (
            await db_session.execute(
                select(DeviceToken).where(DeviceToken.token == "shared-device-fcm")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert str(rows[0].user_id) == str(user_b.id)

    async def test_device_token_requires_auth(self, api_client: AsyncClient):
        """No Authorization header → 401 (per the W4 fix from yesterday)."""
        resp = await api_client.post(
            "/api/v1/auth/device-token",
            json={"token": "anonymous-token", "device_name": "rogue"},
        )
        assert resp.status_code == 401
