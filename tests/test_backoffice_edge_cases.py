"""Edge-case coverage for backoffice endpoints not exercised by
``test_super_admin_backoffice.py``.

Covers:
  • GET  /backoffice/hospitals/{id}/timeline
  • GET  /backoffice/audit-log/export             — CSV streaming
  • GET  /backoffice/settings                     — lazy-create defaults
  • PUT  /backoffice/settings                     — partial update + audit log entry
  • Role enforcement on the new endpoints
"""

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.domains.backoffice.models import (
    HospitalVerificationEvent,
    PlatformSettings,
    SuperAdminActionLog,
    VerificationEventType,
)
from src.domains.users.enums import UserRole
from tests.helpers import (
    login_for_token,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


async def _seed_super_admin(db_session, email_prefix="bo-super"):
    email = unique_email(email_prefix)
    user = await seed_user(
        db_session, email=email, role=UserRole.super_admin, hospital_id=None
    )
    return user, email


# ─── GET /hospitals/{id}/timeline ──────────────────────────────────────────


class TestHospitalTimeline:
    async def test_returns_timeline_events_for_hospital(
        self, api_client: AsyncClient, db_session
    ):
        _, email = await _seed_super_admin(db_session, "tl-super")
        hospital = await seed_hospital(db_session, "Timeline Hospital")

        # Seed two verification events for the hospital
        ev_a = HospitalVerificationEvent(
            hospital_id=hospital.id,
            event_type=VerificationEventType.application_submitted,
            note="App came in",
        )
        ev_b = HospitalVerificationEvent(
            hospital_id=hospital.id,
            event_type=VerificationEventType.approved,
            note="Approved by super-admin",
        )
        db_session.add_all([ev_a, ev_b])
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/timeline",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) == 2
        # Default order is created_at desc — newest first.
        types = [e["event_type"] for e in events]
        assert "Approved" in types
        assert "Application Submitted" in types

    async def test_unknown_hospital_returns_empty_timeline(
        self, api_client: AsyncClient, db_session
    ):
        """Unknown hospital id is not 404 — it returns [] (the timeline
        query returns no rows). This matches the existing endpoint
        contract — caller should distinguish between "no events" and
        "unknown hospital" via the hospital detail endpoint."""
        _, email = await _seed_super_admin(db_session, "tl-empty-super")
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/backoffice/hospitals/00000000-0000-0000-0000-000000000000/timeline",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_non_super_admin_cannot_access_timeline(
        self, api_client: AsyncClient, db_session
    ):
        """Doctor token must be denied — `require_super_admin` is the dep."""
        hospital = await seed_hospital(db_session, "Denied Timeline Hospital")
        email = unique_email("tl-doctor")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, hospital_id=hospital.id
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/timeline",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ─── GET /audit-log/export ──────────────────────────────────────────────────


class TestAuditLogExport:
    async def test_export_returns_csv_attachment(
        self, api_client: AsyncClient, db_session
    ):
        super_admin, email = await _seed_super_admin(db_session, "export-super")
        # Seed an action log entry. NB: SuperAdminActionLog.target_id is
        # String(100) in the DB but the response schema typed it as UUID,
        # so practical values must be real UUIDs to avoid a 500 on read.
        log = SuperAdminActionLog(
            actor_id=super_admin.id,
            action="hospital.approve",
            target_type="hospital",
            target_id="11111111-1111-1111-1111-111111111111",
            note="Approved Hospital X",
        )
        db_session.add(log)
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/backoffice/audit-log/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # CSV content type
        assert "text/csv" in resp.headers.get("content-type", "")
        # Attachment filename header — verifies StreamingResponse wired up correctly
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.headers["content-disposition"].endswith('.csv"')

        # CSV body should contain header row + at least our seeded action.
        body = resp.text
        assert "hospital.approve" in body

    async def test_export_with_action_filter(
        self, api_client: AsyncClient, db_session
    ):
        """`action=...` filter should narrow the export to matching rows."""
        super_admin, email = await _seed_super_admin(db_session, "export-filter")
        db_session.add_all(
            [
                SuperAdminActionLog(
                    actor_id=super_admin.id,
                    action="hospital.approve",
                    target_type="hospital",
                    target_id="22222222-2222-2222-2222-222222222222",
                ),
                SuperAdminActionLog(
                    actor_id=super_admin.id,
                    action="hospital.reject",
                    target_type="hospital",
                    target_id="33333333-3333-3333-3333-333333333333",
                ),
            ]
        )
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/backoffice/audit-log/export?action=hospital.approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "hospital.approve" in body
        assert "hospital.reject" not in body

    async def test_export_denied_for_non_super_admin(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Denied Export Hospital")
        email = unique_email("export-doc")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, hospital_id=hospital.id
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/backoffice/audit-log/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ─── Regression: target_id can be any string (model says String(100)) ─────


class TestAuditLogNonUuidTargetId:
    """Pins the fix for the UUID/str schema mismatch.

    Previously: SuperAdminActionLog.target_id is `String(100)` in the model
    but SuperAdminActionOut.target_id was typed as `UUID`. Any non-UUID row
    in the table would 500 the audit-log GET and CSV export endpoints.

    The fix relaxed the schema to `str | None` to mirror the model. These
    tests would 500 against the old code and ensure the endpoints stay
    forgiving of arbitrary opaque identifiers (configuration keys, etc.)
    that the model deliberately permits.
    """

    async def test_audit_log_get_handles_non_uuid_target_id(
        self, api_client: AsyncClient, db_session
    ):
        super_admin, email = await _seed_super_admin(db_session, "non-uuid-get")
        # Seed a row whose target_id is intentionally NOT a UUID — exactly
        # the kind of value the String(100) column was designed to accept
        # for non-entity targets.
        log = SuperAdminActionLog(
            actor_id=super_admin.id,
            action="platform_settings.update",
            target_type="settings",
            target_id="platform_default",  # not a UUID — used to crash the response
            note="Updated platform default region",
        )
        db_session.add(log)
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/backoffice/audit-log",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Pre-fix: 500. Post-fix: 200 with the row included.
        assert resp.status_code == 200
        rows = resp.json()
        matches = [r for r in rows if r.get("target_id") == "platform_default"]
        assert len(matches) == 1
        assert matches[0]["target_type"] == "settings"

    async def test_audit_log_export_handles_non_uuid_target_id(
        self, api_client: AsyncClient, db_session
    ):
        """Same regression check on the CSV export — both endpoints share
        the same service function, so both must survive non-UUID rows."""
        super_admin, email = await _seed_super_admin(db_session, "non-uuid-export")
        db_session.add(
            SuperAdminActionLog(
                actor_id=super_admin.id,
                action="platform_settings.update",
                target_type="settings",
                target_id="some-arbitrary-config-key",
                note="Tweaked a feature flag",
            )
        )
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/backoffice/audit-log/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "some-arbitrary-config-key" in resp.text

    async def test_audit_log_handles_mix_of_uuid_and_non_uuid_target_ids(
        self, api_client: AsyncClient, db_session
    ):
        """Mixed rows must all serialise — neither type should poison the
        other's response."""
        super_admin, email = await _seed_super_admin(db_session, "non-uuid-mixed")
        db_session.add_all(
            [
                SuperAdminActionLog(
                    actor_id=super_admin.id,
                    action="hospital.approve",
                    target_type="hospital",
                    target_id="44444444-4444-4444-4444-444444444444",
                ),
                SuperAdminActionLog(
                    actor_id=super_admin.id,
                    action="platform_settings.update",
                    target_type="settings",
                    target_id="platform_default",
                ),
            ]
        )
        await db_session.commit()

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/backoffice/audit-log",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        target_ids = {r.get("target_id") for r in resp.json()}
        assert "44444444-4444-4444-4444-444444444444" in target_ids
        assert "platform_default" in target_ids


# ─── GET / PUT /settings ────────────────────────────────────────────────────


class TestPlatformSettings:
    async def test_get_settings_lazy_creates_default_row(
        self, api_client: AsyncClient, db_session
    ):
        """First GET on a fresh DB should auto-create the singleton row
        and return defaults. Subsequent GETs should return the same row."""
        _, email = await _seed_super_admin(db_session, "settings-lazy")
        token = await login_for_token(api_client, email)

        first = await api_client.get(
            "/api/v1/backoffice/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200
        body = first.json()
        assert body["platform_name"] == "Pat-Stat"

        # Verify there's exactly ONE row (singleton invariant).
        async with AsyncSessionLocal() as fresh:
            rows = (await fresh.execute(select(PlatformSettings))).scalars().all()
        assert len(rows) == 1

        # A second GET should not create another row.
        second = await api_client.get(
            "/api/v1/backoffice/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 200

        async with AsyncSessionLocal() as fresh:
            rows = (await fresh.execute(select(PlatformSettings))).scalars().all()
        assert len(rows) == 1

    async def test_put_partial_update_preserves_other_fields(
        self, api_client: AsyncClient, db_session
    ):
        _, email = await _seed_super_admin(db_session, "settings-partial")
        token = await login_for_token(api_client, email)

        # Bootstrap by setting both fields.
        await api_client.put(
            "/api/v1/backoffice/settings",
            json={
                "platform_name": "MyHospitalPlatform",
                "support_email": "support@example.com",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Now PATCH only support_email — platform_name should remain.
        resp = await api_client.put(
            "/api/v1/backoffice/settings",
            json={"support_email": "new-support@example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["support_email"] == "new-support@example.com"
        assert body["platform_name"] == "MyHospitalPlatform"

    async def test_put_writes_audit_log_entry(
        self, api_client: AsyncClient, db_session
    ):
        """Updating settings should leave a trail in super_admin_action_logs
        with action='platform_settings.update'."""
        super_admin, email = await _seed_super_admin(db_session, "settings-audit")
        token = await login_for_token(api_client, email)

        await api_client.put(
            "/api/v1/backoffice/settings",
            json={"platform_name": "AuditedPlatform"},
            headers={"Authorization": f"Bearer {token}"},
        )

        async with AsyncSessionLocal() as fresh:
            rows = (
                await fresh.execute(
                    select(SuperAdminActionLog).where(
                        SuperAdminActionLog.action == "platform_settings.update",
                        SuperAdminActionLog.actor_id == super_admin.id,
                    )
                )
            ).scalars().all()
        assert len(rows) >= 1
        latest = rows[-1]
        # Changes blob should mention platform_name.
        assert "platform_name" in str(latest.action_metadata)

    async def test_put_invalid_email_returns_422(
        self, api_client: AsyncClient, db_session
    ):
        """`support_email` is `EmailStr` — a malformed value should 422."""
        _, email = await _seed_super_admin(db_session, "settings-bad-email")
        token = await login_for_token(api_client, email)

        resp = await api_client.put(
            "/api/v1/backoffice/settings",
            json={"support_email": "not-an-email"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    async def test_put_denied_for_non_super_admin(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "PUT Settings Denied Hospital")
        email = unique_email("settings-doc")
        await seed_user(
            db_session, email=email, role=UserRole.doctor, hospital_id=hospital.id
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.put(
            "/api/v1/backoffice/settings",
            json={"platform_name": "Hijack"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
