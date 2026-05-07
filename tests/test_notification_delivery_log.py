"""Tests for the new ``NotificationLog`` delivery columns and the
end-to-end behaviour of ``notify_family_of_update``.

The Celery task is invoked in-process (no broker) by calling its
underlying function. FCM is mocked so no network calls happen.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.tasks.celery_app import celery_app


@pytest.fixture
def celery_eager():
    """Run Celery tasks inline (no broker, no worker).

    ``notify_family_of_update`` enqueues ``_send_fcm_multicast`` via
    ``.delay()``. Without eager mode the second task is sent to the
    broker and never executes inside the test, so we never observe the
    FCM mock being called. Eager mode flips ``.delay()`` into a
    synchronous call.
    """
    prev_eager = celery_app.conf.task_always_eager
    prev_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prev_eager
        celery_app.conf.task_eager_propagates = prev_propagate

from src.domains.notifications import policy
from src.domains.patients.models import (
    FamilyPatientLink,
    NotificationDeliveryStatus,
    NotificationLog,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import DeviceToken
from tests.helpers import (
    seed_admission,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


# ─── ORM-level: schema columns reachable through the model ─────────────────


class TestNotificationLogColumns:
    """Confirms the migration columns are wired into the ORM correctly."""

    async def test_can_create_log_row_with_all_delivery_fields(self, db_session):
        hospital = await seed_hospital(db_session, "Log Hospital")
        user = await seed_user(
            db_session,
            email=unique_email("log-user"),
            role=UserRole.family,
            hospital_id=None,
        )

        log = NotificationLog(
            user_id=user.id,
            title="Test",
            body="Test body",
            category="critical_alert",
            delivery_status=NotificationDeliveryStatus.queued.value,
            deferred_until=None,
        )
        db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        # Fetch and verify all five new columns are addressable.
        fetched = (
            await db_session.execute(select(NotificationLog).where(NotificationLog.id == log.id))
        ).scalar_one()
        assert fetched.delivery_status == "queued"
        assert fetched.delivered_at is None
        assert fetched.deferred_until is None
        assert fetched.fcm_message_ids is None
        assert fetched.last_error is None

    async def test_can_persist_all_delivery_status_values(self, db_session):
        """Each enum value should round-trip through the column without error."""
        hospital = await seed_hospital(db_session, "Status Hospital")
        user = await seed_user(
            db_session,
            email=unique_email("status-user"),
            role=UserRole.family,
        )
        for status in NotificationDeliveryStatus:
            log = NotificationLog(
                user_id=user.id,
                title="t",
                body="b",
                category="general",
                delivery_status=status.value,
            )
            db_session.add(log)
        await db_session.flush()
        await db_session.commit()

        rows = (
            await db_session.execute(
                select(NotificationLog.delivery_status).where(
                    NotificationLog.user_id == user.id
                )
            )
        ).all()
        statuses = {r[0] for r in rows}
        assert statuses == {s.value for s in NotificationDeliveryStatus}


# ─── notify_family_of_update behaviour ─────────────────────────────────────


def _seed_family_with_devices_sync(
    patient_id: str, user_id: str, device_tokens: list[str]
) -> None:
    """Synchronous helper — the Celery task uses a sync session, so we
    seed the family link and device tokens via the same session class
    the task itself uses. Wrapped sync DB so the task's view of the
    database matches what we set up.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.core.config import settings

    engine = create_engine(settings.DATABASE_URL_SYNC)
    with Session(engine) as s:
        s.add(
            FamilyPatientLink(
                patient_id=str(patient_id),
                family_user_id=str(user_id),
            )
        )
        for tok in device_tokens:
            s.add(
                DeviceToken(
                    user_id=str(user_id),
                    token=tok,
                    device_name="test-device",
                )
            )
        s.commit()


class TestNotifyFamilyOfUpdate:
    """Drive the Celery task in-process and assert what it persisted."""

    async def test_critical_event_writes_queued_log(self, db_session, celery_eager):
        # Seed: hospital, doctor, patient, family member with device.
        hospital = await seed_hospital(db_session, "NF Hospital A")
        doctor = await seed_user(
            db_session,
            email=unique_email("nf-doc"),
            role=UserRole.doctor,
            full_name="Dr. NF",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="NF Patient",
            primary_doctor_id=doctor.id,
        )
        family = await seed_user(
            db_session,
            email=unique_email("nf-fam"),
            role=UserRole.family,
        )
        _seed_family_with_devices_sync(patient.id, family.id, ["fake-fcm-token-1"])

        # Mock the FCM provider so no network call is made and the multicast
        # reports a single success.
        from src.tasks import notifications as notif_tasks

        with patch.object(
            notif_tasks,
            "send_multicast",
            return_value={
                "success": 1,
                "failure": 0,
                "invalid_tokens": [],
                "message_ids": ["fcm-msg-id-1"],
            },
        ):
            # Call the task as a normal function (no broker, no worker).
            counters = notif_tasks.notify_family_of_update(
                patient_id=str(patient.id),
                patient_name="NF Patient",
                new_status="Critical",
                update_id=None,
                note_preview="Patient unresponsive",
                author_name="Dr. NF",
                event_kind=policy.EVENT_EMERGENCY_FLAG,
            )

        assert counters["recipients"] == 1
        assert counters["queued_push"] == 1
        assert counters["skipped_routine"] == 0
        # ``deferred`` counter was removed in v1 (no quiet-hours scheduling).
        assert "deferred" not in counters

        # Verify the log row exists and was stamped as sent (mock returned success).
        rows = (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.user_id == family.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        log = rows[0]
        assert log.category == "critical_alert"
        # With celery_eager fixture, the FCM subtask runs synchronously, so the
        # row must be stamped 'sent' (or 'failed' on a real send error — here we
        # mocked a success).
        assert log.delivery_status == NotificationDeliveryStatus.sent.value
        assert log.delivered_at is not None
        assert log.fcm_message_ids == "fcm-msg-id-1"

    async def test_routine_event_skips_push(self, db_session):
        hospital = await seed_hospital(db_session, "NF Hospital B")
        doctor = await seed_user(
            db_session,
            email=unique_email("nf-doc-r"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Routine Patient",
            primary_doctor_id=doctor.id,
        )
        family = await seed_user(
            db_session,
            email=unique_email("nf-fam-r"),
            role=UserRole.family,
        )
        _seed_family_with_devices_sync(patient.id, family.id, ["routine-token"])

        from src.tasks import notifications as notif_tasks

        with patch.object(notif_tasks, "send_multicast") as mock_send:
            counters = notif_tasks.notify_family_of_update(
                patient_id=str(patient.id),
                patient_name="Routine Patient",
                new_status="Stable",
                update_id=None,
                note_preview="Vitals normal",
                author_name="Dr. NF",
                event_kind=policy.EVENT_VITALS_ONLY,
            )
            # FCM should NOT be called for routine events.
            mock_send.assert_not_called()

        assert counters["recipients"] == 1
        assert counters["skipped_routine"] == 1
        assert counters["queued_push"] == 0

        rows = (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.user_id == family.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].delivery_status == NotificationDeliveryStatus.skipped_routine.value
        assert rows[0].category == "general"

    async def test_no_devices_marks_no_devices(self, db_session):
        """A queued event for someone with no DeviceToken rows is logged but not pushed."""
        hospital = await seed_hospital(db_session, "NF Hospital C")
        doctor = await seed_user(
            db_session,
            email=unique_email("nf-doc-nd"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Devicerless Patient",
            primary_doctor_id=doctor.id,
        )
        family = await seed_user(
            db_session,
            email=unique_email("nf-fam-nd"),
            role=UserRole.family,
        )
        _seed_family_with_devices_sync(patient.id, family.id, [])  # no devices

        from src.tasks import notifications as notif_tasks

        with patch.object(notif_tasks, "send_multicast") as mock_send:
            counters = notif_tasks.notify_family_of_update(
                patient_id=str(patient.id),
                patient_name="Devicerless Patient",
                new_status="Critical",
                update_id=None,
                note_preview="Test",
                author_name="Dr. NF",
                event_kind=policy.EVENT_EMERGENCY_FLAG,
            )
            mock_send.assert_not_called()

        assert counters["no_devices"] == 1
        assert counters["queued_push"] == 0

        rows = (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.user_id == family.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].delivery_status == NotificationDeliveryStatus.no_devices.value


# ─── Stored body sanitisation (W1 fix) ─────────────────────────────────────


class TestStoredBodyDoesNotCarryClinicalNote:
    """The stored ``NotificationLog.body`` must not contain the free-text
    clinical note. The note lives in ``clinical_updates.note`` behind RBAC;
    the bell row references it via ``update_id``.
    """

    async def test_stored_body_excludes_note_preview(self, db_session, celery_eager):
        hospital = await seed_hospital(db_session, "Stored-Body Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("sb-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Patient X",
            primary_doctor_id=doctor.id,
        )
        family = await seed_user(
            db_session,
            email=unique_email("sb-fam"),
            role=UserRole.family,
        )
        _seed_family_with_devices_sync(patient.id, family.id, ["sb-token"])

        clinical_note = (
            "BP 200/100, patient unresponsive, escalating to ICU — DO NOT STORE THIS IN BODY"
        )

        from src.tasks import notifications as notif_tasks

        with patch.object(
            notif_tasks,
            "send_multicast",
            return_value={
                "success": 1,
                "failure": 0,
                "invalid_tokens": [],
                "message_ids": ["fcm-1"],
            },
        ):
            notif_tasks.notify_family_of_update(
                patient_id=str(patient.id),
                patient_name="Patient X",
                new_status="Critical",
                update_id=None,
                note_preview=clinical_note,
                author_name="Dr. NF",
                event_kind=policy.EVENT_EMERGENCY_FLAG,
            )

        rows = (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.user_id == family.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        log = rows[0]

        # The clinical note text MUST NOT appear in the stored body — that
        # field is a candidate for export to BI/analytics tools and we
        # don't want clinical free text leaving the OLTP boundary.
        assert "BP 200/100" not in log.body
        assert "DO NOT STORE THIS IN BODY" not in log.body
        assert "unresponsive" not in log.body
        # Body should be a short generic string with a "tap to view" cue.
        assert "tap" in log.body.lower() or "view" in log.body.lower()


# ─── PHI sanitisation ──────────────────────────────────────────────────────


class TestPHISanitisation:
    """Ensures patient names and clinical notes never reach the visible
    FCM payload (title/body) — they only travel in the encrypted ``data``
    section that's revealed after device unlock.
    """

    async def test_visible_payload_carries_no_phi(self, db_session, celery_eager):
        hospital = await seed_hospital(db_session, "PHI Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("phi-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Mr. Sensitive Name",
            primary_doctor_id=doctor.id,
        )
        family = await seed_user(
            db_session,
            email=unique_email("phi-fam"),
            role=UserRole.family,
        )
        _seed_family_with_devices_sync(patient.id, family.id, ["phi-token"])

        from src.tasks import notifications as notif_tasks

        captured: dict = {}

        def capture(tokens, title, body, data=None):
            captured["title"] = title
            captured["body"] = body
            captured["data"] = data
            return {
                "success": 1,
                "failure": 0,
                "invalid_tokens": [],
                "message_ids": ["fcm-id-1"],
            }

        with patch.object(notif_tasks, "send_multicast", side_effect=capture):
            notif_tasks.notify_family_of_update(
                patient_id=str(patient.id),
                patient_name="Mr. Sensitive Name",
                new_status="Critical",
                update_id=None,
                note_preview="Detailed clinical information that must not leak",
                author_name="Dr. Confidential",
                event_kind=policy.EVENT_EMERGENCY_FLAG,
            )

        # Visible title/body MUST NOT contain PHI.
        assert "Mr. Sensitive Name" not in captured["title"]
        assert "Mr. Sensitive Name" not in captured["body"]
        assert "Detailed clinical information" not in captured["title"]
        assert "Detailed clinical information" not in captured["body"]
        # The data section is fine — it's only revealed after device unlock.
        assert captured["data"]["patient_name"] == "Mr. Sensitive Name"
        assert captured["data"]["author_name"] == "Dr. Confidential"
        assert captured["data"]["event_kind"] == policy.EVENT_EMERGENCY_FLAG
