"""Edge-case coverage for the notifications endpoints.

The existing `test_notifications.py` covers the happy paths. This file
adds the gaps:

  • PATCH /notifications/{id}/read — idempotency, cross-user denial
  • POST  /notifications/read-all  — category filter, no-unread no-op
  • GET   /notifications/unread-count?category=... — per-tab badge counts
  • GET   /notifications?category=... — tab filter
  • Cross-user isolation — user A cannot see/modify user B's notifications
"""

from datetime import datetime, timezone

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.domains.patients.models import NotificationLog
from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_user, unique_email

pytestmark = pytest.mark.asyncio


async def _seed_notification(
    session, user_id: str, *, title="N", body="b", category="general", is_read=False
) -> NotificationLog:
    n = NotificationLog(
        user_id=user_id,
        title=title,
        body=body,
        category=category,
        is_read=is_read,
    )
    session.add(n)
    await session.flush()
    await session.commit()
    return n


# ─── PATCH /{id}/read ──────────────────────────────────────────────────────


class TestMarkSingleRead:
    async def test_idempotent_double_read(self, api_client: AsyncClient, db_session):
        """Marking the same notification read twice should both 200, and
        ``read_at`` should NOT shift on the second call (the handler skips
        the assignment if already-read)."""
        email = unique_email("idempotent-read")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        notif = await _seed_notification(db_session, user.id)
        token = await login_for_token(api_client, email)

        first = await api_client.patch(
            f"/api/v1/notifications/{notif.id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200
        first_read_at = first.json()["read_at"]
        assert first.json()["is_read"] is True

        second = await api_client.patch(
            f"/api/v1/notifications/{notif.id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 200
        # read_at should NOT have moved on the no-op second call.
        assert second.json()["read_at"] == first_read_at

    async def test_user_a_cannot_mark_user_b_notification_read(
        self, api_client: AsyncClient, db_session
    ):
        """The query in mark_notification_read filters by user_id — anyone
        else trying to PATCH a notification they don't own gets 404, not 403,
        because the row is invisible to them entirely (correct: avoids
        leaking the existence of that notification id)."""
        # User A owns the notification.
        user_a = await seed_user(
            db_session, email=unique_email("notif-owner"), role=UserRole.doctor
        )
        notif = await _seed_notification(db_session, user_a.id)

        # User B is a different person, tries to mark it read.
        email_b = unique_email("notif-attacker")
        await seed_user(db_session, email=email_b, role=UserRole.doctor)
        token_b = await login_for_token(api_client, email_b)

        resp = await api_client.patch(
            f"/api/v1/notifications/{notif.id}/read",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

        # Confirm via fresh session that the notification is STILL unread.
        async with AsyncSessionLocal() as fresh:
            row = (
                await fresh.execute(
                    select(NotificationLog).where(NotificationLog.id == notif.id)
                )
            ).scalar_one()
        assert row.is_read is False

    async def test_unknown_notification_id_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("notif-404")
        await seed_user(db_session, email=email, role=UserRole.nurse)
        token = await login_for_token(api_client, email)

        resp = await api_client.patch(
            "/api/v1/notifications/00000000-0000-0000-0000-000000000000/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ─── POST /read-all ────────────────────────────────────────────────────────


class TestMarkAllRead:
    async def test_read_all_with_category_filter(
        self, api_client: AsyncClient, db_session
    ):
        """``?category=critical_alert`` should mark only critical alerts
        as read; other tabs stay unread (per-tab "mark all read" UX)."""
        email = unique_email("read-all-cat")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        # Seed two different categories.
        await _seed_notification(db_session, user.id, category="critical_alert")
        await _seed_notification(db_session, user.id, category="critical_alert")
        await _seed_notification(db_session, user.id, category="shift_log")
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/notifications/read-all?category=critical_alert",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Verify via list endpoint: critical = 0 unread, shift_log = 1 unread.
        crit_unread = await api_client.get(
            "/api/v1/notifications/unread-count?category=critical_alert",
            headers={"Authorization": f"Bearer {token}"},
        )
        shift_unread = await api_client.get(
            "/api/v1/notifications/unread-count?category=shift_log",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert crit_unread.json()["count"] == 0
        assert shift_unread.json()["count"] == 1

    async def test_read_all_with_no_unread_is_safe_noop(
        self, api_client: AsyncClient, db_session
    ):
        """Calling read-all when nothing is unread should still 200,
        not 500 or empty-update-error."""
        email = unique_email("read-all-noop")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        # Seed only already-read notifications.
        await _seed_notification(db_session, user.id, is_read=True)
        token = await login_for_token(api_client, email)

        resp = await api_client.post(
            "/api/v1/notifications/read-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_read_all_only_affects_current_user(
        self, api_client: AsyncClient, db_session
    ):
        """User A's read-all must NOT touch user B's notifications."""
        # User A
        email_a = unique_email("read-all-isolation-a")
        user_a = await seed_user(db_session, email=email_a, role=UserRole.doctor)
        await _seed_notification(db_session, user_a.id)

        # User B
        email_b = unique_email("read-all-isolation-b")
        user_b = await seed_user(db_session, email=email_b, role=UserRole.doctor)
        b_notif = await _seed_notification(db_session, user_b.id)

        token_a = await login_for_token(api_client, email_a)
        await api_client.post(
            "/api/v1/notifications/read-all",
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B's notification must still be unread.
        async with AsyncSessionLocal() as fresh:
            row = (
                await fresh.execute(
                    select(NotificationLog).where(NotificationLog.id == b_notif.id)
                )
            ).scalar_one()
        assert row.is_read is False


# ─── GET /unread-count?category=... ────────────────────────────────────────


class TestUnreadCountByCategory:
    async def test_category_scoped_count(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("count-by-cat")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        await _seed_notification(db_session, user.id, category="critical_alert")
        await _seed_notification(db_session, user.id, category="critical_alert")
        await _seed_notification(db_session, user.id, category="shift_log")
        await _seed_notification(db_session, user.id, category="general")
        token = await login_for_token(api_client, email)

        crit = await api_client.get(
            "/api/v1/notifications/unread-count?category=critical_alert",
            headers={"Authorization": f"Bearer {token}"},
        )
        gen = await api_client.get(
            "/api/v1/notifications/unread-count?category=general",
            headers={"Authorization": f"Bearer {token}"},
        )
        all_ = await api_client.get(
            "/api/v1/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert crit.json()["count"] == 2
        assert gen.json()["count"] == 1
        assert all_.json()["count"] == 4

    async def test_count_for_unknown_category_is_zero(
        self, api_client: AsyncClient, db_session
    ):
        """A typo'd category just returns 0 — not 422 — which is the right
        behaviour for a frontend that reads a server-provided category list."""
        email = unique_email("count-unknown-cat")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        await _seed_notification(db_session, user.id, category="general")
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/notifications/unread-count?category=nonexistent_tab",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ─── GET /notifications?category=... ───────────────────────────────────────


class TestListByCategory:
    async def test_list_filtered_by_category(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("list-by-cat")
        user = await seed_user(db_session, email=email, role=UserRole.doctor)
        await _seed_notification(db_session, user.id, category="critical_alert", title="CRIT 1")
        await _seed_notification(db_session, user.id, category="shift_log", title="SHIFT 1")
        await _seed_notification(db_session, user.id, category="critical_alert", title="CRIT 2")
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/notifications?category=critical_alert",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert all(r["category"] == "critical_alert" for r in rows)
