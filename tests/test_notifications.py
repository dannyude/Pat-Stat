"""Integration tests for the Notifications endpoints."""

from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.patients.models import NotificationLog
from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_user, unique_email

pytestmark = pytest.mark.asyncio


async def _seed_notifications(
    session: AsyncSession, user_id: str, count: int = 3
) -> list:
    """Seed notification records directly in the database."""
    notifs = []
    for i in range(count):
        n = NotificationLog(
            user_id=user_id,
            title=f"Notification {i + 1}",
            body=f"Body of notification {i + 1}",
            is_read=False,
        )
        session.add(n)
        notifs.append(n)
    await session.flush()
    await session.commit()
    return notifs


class TestNotifications:
    async def test_list_notifications(self, api_client: AsyncClient, db_session):
        email = unique_email("notif-user")
        user = await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Dr. Notif"
        )
        await _seed_notifications(db_session, user.id, count=3)

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3
        assert all("title" in n for n in data)

    async def test_unread_count(self, api_client: AsyncClient, db_session):
        email = unique_email("notif-count")
        user = await seed_user(
            db_session, email=email, role=UserRole.nurse, full_name="Nurse Notif"
        )
        await _seed_notifications(db_session, user.id, count=5)

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert resp.json()["count"] >= 5

    async def test_mark_single_notification_read(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("notif-read")
        user = await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Dr. Reader"
        )
        notifs = await _seed_notifications(db_session, user.id, count=1)
        notif_id = notifs[0].id

        token = await login_for_token(api_client, email)

        # Mark as read
        resp = await api_client.patch(
            f"/api/v1/notifications/{notif_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True
        assert resp.json()["read_at"] is not None

        # Idempotent re-read
        resp2 = await api_client.patch(
            f"/api/v1/notifications/{notif_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200

    async def test_mark_all_read(self, api_client: AsyncClient, db_session):
        email = unique_email("notif-all-read")
        user = await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Dr. AllRead"
        )
        await _seed_notifications(db_session, user.id, count=4)

        token = await login_for_token(api_client, email)

        # Mark all as read
        resp = await api_client.post(
            "/api/v1/notifications/read-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Verify count is 0
        count_resp = await api_client.get(
            "/api/v1/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert count_resp.json()["count"] == 0

    async def test_notification_not_found(self, api_client: AsyncClient, db_session):
        email = unique_email("notif-404")
        await seed_user(db_session, email=email, role=UserRole.doctor)

        token = await login_for_token(api_client, email)
        resp = await api_client.patch(
            "/api/v1/notifications/00000000-0000-0000-0000-000000000000/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_unread_only_filter(self, api_client: AsyncClient, db_session):
        email = unique_email("notif-filter")
        user = await seed_user(
            db_session, email=email, role=UserRole.doctor, full_name="Dr. Filter"
        )
        notifs = await _seed_notifications(db_session, user.id, count=3)

        token = await login_for_token(api_client, email)

        # Mark one as read
        await api_client.patch(
            f"/api/v1/notifications/{notifs[0].id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Filter unread only
        resp = await api_client.get(
            "/api/v1/notifications?unread_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(n["is_read"] is False for n in data)
