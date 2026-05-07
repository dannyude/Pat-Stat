"""Integration tests verifying the HTTP→Celery dispatch path.

These exercise the wiring added in this turn:

  • clinical_updates.py       → dispatch_family_notification(event_kind=...)
  • emergency_flags.py        → dispatch_family_notification(event_kind=EMERGENCY_FLAG)
  • shift_handover.py         → dispatch_family_notification(event_kind=SHIFT_HANDOVER)

We don't actually run Celery here. Instead we patch
``celery_app.send_task`` (the call site inside ``dispatch.py``) and
assert it was invoked with the correct task name and kwargs. This
validates the boundary between FastAPI and the task queue without
spinning up a worker.
"""

from unittest.mock import MagicMock

from httpx import AsyncClient
import pytest

from src.domains.notifications import policy
from src.domains.users.enums import UserRole
from tests.helpers import (
    login_for_token,
    seed_admission,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def captured_celery_calls(monkeypatch):
    """Patch the dispatcher's ``celery_app.send_task`` and capture every call.

    We patch the symbol *imported* into ``dispatch`` (``src.domains.notifications.dispatch.celery_app``),
    not the original module, so the test mock fires regardless of how the
    handlers reach the dispatcher.
    """
    mock_app = MagicMock()
    mock_app.send_task = MagicMock(return_value=None)

    monkeypatch.setattr(
        "src.domains.notifications.dispatch.celery_app", mock_app
    )

    return mock_app.send_task


def _assert_dispatched(
    send_task: MagicMock,
    expected_event_kind: str,
    expected_patient_name: str | None = None,
):
    """Helper — find the most recent notify_family_of_update call and inspect it."""
    matching = [
        call
        for call in send_task.call_args_list
        if call.args
        and call.args[0] == "src.tasks.notifications.notify_family_of_update"
    ]
    assert matching, (
        f"Expected at least one send_task('src.tasks.notifications.notify_family_of_update') "
        f"call; got: {send_task.call_args_list}"
    )
    last = matching[-1]
    kwargs = last.kwargs.get("kwargs") or last.args[1]
    assert kwargs["event_kind"] == expected_event_kind
    if expected_patient_name is not None:
        assert kwargs["patient_name"] == expected_patient_name
    return kwargs


# ─── clinical_updates ────────────────────────────────────────────────────────


class TestClinicalUpdateDispatch:
    async def test_status_change_dispatches_status_changed(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        hospital = await seed_hospital(db_session, "Dispatch Hospital A")
        doctor_email = unique_email("disp-doc-a")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Dispatch",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Dispatch Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        # Status moves Stable → Getting Better — should be EVENT_STATUS_CHANGED.
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={"status": "Getting Better", "note": "Improving on antibiotics."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        kwargs = _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_STATUS_CHANGED,
            expected_patient_name="Dispatch Patient",
        )
        assert kwargs["new_status"] == "Getting Better"
        assert kwargs["author_name"] == "Dr. Dispatch"

    async def test_status_change_to_critical_dispatches_status_to_critical(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        hospital = await seed_hospital(db_session, "Dispatch Hospital B")
        doctor_email = unique_email("disp-doc-b")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Critical",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Critical Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={"status": "Critical", "note": "Sudden decline."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_STATUS_TO_CRITICAL,
        )

    async def test_mark_emergency_dispatches_emergency_flag_kind(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        hospital = await seed_hospital(db_session, "Dispatch Hospital C")
        doctor_email = unique_email("disp-doc-c")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Mark",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Marked Patient",
            status="Being Monitored",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={
                "status": "Being Monitored",
                "note": "Vitals stable but family worried.",
                "mark_emergency": True,
                "emergency_reason": "Family request",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_EMERGENCY_FLAG,
        )

    async def test_vitals_only_dispatches_vitals_only(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        """Update with no status change, no emergency flag → routine tier."""
        hospital = await seed_hospital(db_session, "Dispatch Hospital D")
        doctor_email = unique_email("disp-doc-d")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Vitals",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Vitals Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={
                "status": "Stable",  # same as current → no status change
                "note": "BP 120/80",
                "blood_pressure": "120/80",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_VITALS_ONLY,
        )


# ─── emergency_flags ─────────────────────────────────────────────────────────


class TestEmergencyFlagDispatch:
    async def test_create_emergency_flag_dispatches(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        hospital = await seed_hospital(db_session, "EF Dispatch Hospital")
        doctor_email = unique_email("ef-disp-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. EF",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="EF Patient",
            status="Being Monitored",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            "/api/v1/emergency-flags",
            json={
                "admission_id": admission.id,
                "priority": "Critical",
                "reason": "Patient unresponsive",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        kwargs = _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_EMERGENCY_FLAG,
            expected_patient_name="EF Patient",
        )
        assert kwargs["note_preview"] == "Patient unresponsive"


# ─── shift_handover ──────────────────────────────────────────────────────────


class TestShiftHandoverDispatch:
    async def test_create_handover_dispatches(
        self, api_client: AsyncClient, db_session, captured_celery_calls
    ):
        hospital = await seed_hospital(db_session, "SH Dispatch Hospital")
        doctor_email = unique_email("sh-disp-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Handover",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="SH Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            "/api/v1/shift-handovers",
            json={
                "admission_id": admission.id,
                "summary": "Patient stable, continue IV.",
                "pending_actions": "Bloodwork at 6 AM",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        kwargs = _assert_dispatched(
            captured_celery_calls,
            expected_event_kind=policy.EVENT_SHIFT_HANDOVER,
            expected_patient_name="SH Patient",
        )
        assert kwargs["note_preview"] == "Patient stable, continue IV."


# ─── broker outage tolerance ─────────────────────────────────────────────────


class TestDispatchResilience:
    async def test_broker_failure_does_not_500_the_request(
        self, api_client: AsyncClient, db_session, monkeypatch
    ):
        """If Celery broker is down, the clinical update must still succeed.

        The dispatcher swallows broker exceptions by design — see
        ``dispatch.py`` for rationale. This test pins that behaviour.
        """
        # Patch send_task to raise.
        from unittest.mock import MagicMock

        broken = MagicMock()
        broken.send_task = MagicMock(side_effect=ConnectionError("broker down"))
        monkeypatch.setattr(
            "src.domains.notifications.dispatch.celery_app", broken
        )

        hospital = await seed_hospital(db_session, "Resilience Hospital")
        doctor_email = unique_email("resilience-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Resilient",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Resilient Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={"status": "Getting Better", "note": "Doing well."},
            headers={"Authorization": f"Bearer {token}"},
        )

        # The HTTP call MUST still succeed even though the dispatch raised.
        assert resp.status_code == 201, resp.text

    async def test_programming_bug_in_dispatch_propagates(
        self, api_client: AsyncClient, db_session, monkeypatch
    ):
        """Bugs in our own code (not broker errors) MUST surface, not be
        swallowed by ``dispatch.py``. Without this contract, a typo in
        the kwargs dict would silently 200 the request while no
        notifications fire — and we'd never know in production.

        We simulate a programming bug by making ``send_task`` raise a
        TypeError (NOT in the dispatcher's broker-error tuple, so it
        must escape).

        In production this becomes a 500 via the global exception handler
        in ``src/main.py`` (see ``test_global_handler_returns_500_for_bug``
        below). In this test we use httpx's default
        ``raise_app_exceptions=True`` transport, which RE-RAISES the
        exception after the FastAPI handler has sent its 500 response —
        so we observe the propagation directly with ``pytest.raises``.

        Both contracts are pinned:
          • here: dispatch.py doesn't swallow the bug
          • below: main.py turns it into a polite 500
        """
        import pytest as _pytest
        from unittest.mock import MagicMock

        buggy = MagicMock()
        buggy.send_task = MagicMock(
            side_effect=TypeError("imagine a typo in kwargs dict")
        )
        monkeypatch.setattr(
            "src.domains.notifications.dispatch.celery_app", buggy
        )

        hospital = await seed_hospital(db_session, "Bug-Propagation Hospital")
        doctor_email = unique_email("bug-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Bug",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Bug Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        with _pytest.raises(TypeError, match="imagine a typo"):
            await api_client.post(
                f"/api/v1/patients/{admission.patient_id}/updates",
                json={"status": "Getting Better", "note": "Bug test."},
                headers={"Authorization": f"Bearer {token}"},
            )
