"""Edge-case coverage for patient endpoints not exercised by test_patients_crud.py.

Covers:
  • GET    /api/v1/patients/{id}                — retrieve single
  • PATCH  /api/v1/patients/{id}                — partial update
  • POST   /api/v1/patients/{id}/discharge      — discharge + cascade
  • Hospital scope leakage — staff at hospital A must not see hospital B's patients
  • Discharged patient queries — must 404 (no active admission)
"""

from datetime import datetime, timezone

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.domains.patients.models import Admission, EmergencyFlag
from src.domains.patients.enums import PatientStatus
from src.domains.users.enums import UserRole
from tests.helpers import (
    login_for_token,
    seed_admission,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


# ─── GET /patients/{id} ─────────────────────────────────────────────────────


class TestGetSinglePatient:
    async def test_get_patient_returns_full_admission(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Get-Patient Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("get-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Single Patient",
            ward="ICU",
            bed_number="ICU-1",
            diagnosis="Sepsis",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor.email)
        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["full_name"] == "Single Patient"
        assert body["ward"] == "ICU"
        assert body["diagnosis"] == "Sepsis"
        assert body["is_active"] is True

    async def test_get_unknown_patient_id_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "404 Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("404-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.get(
            "/api/v1/patients/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_get_patient_from_other_hospital_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Hospital scope leakage check — A doctor at Hospital A must NOT see
        a patient admitted at Hospital B, even by guessing the UUID."""
        hospital_a = await seed_hospital(db_session, "Hospital A")
        hospital_b = await seed_hospital(db_session, "Hospital B")

        doctor_a = await seed_user(
            db_session,
            email=unique_email("scope-doc-a"),
            role=UserRole.doctor,
            hospital_id=hospital_a.id,
        )
        # Patient admitted at hospital B
        patient_b, _ = await seed_admission(
            db_session,
            hospital_id=hospital_b.id,
            full_name="B Patient",
        )

        token_a = await login_for_token(api_client, doctor_a.email)
        resp = await api_client.get(
            f"/api/v1/patients/{patient_b.id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        # Must NOT be 200 — returning 200 here would be a HIPAA-grade leak.
        assert resp.status_code == 404


# ─── PATCH /patients/{id} ───────────────────────────────────────────────────


class TestPatchPatient:
    async def test_patch_status_updates_admission(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Patch Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("patch-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            status="Stable",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"status": "Critical", "ward": "ICU"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "Critical"
        assert body["ward"] == "ICU"

    async def test_patch_partial_update_preserves_other_fields(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Partial Patch Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("partial-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Original Name",
            ward="Ward A",
            diagnosis="Original Dx",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor.email)

        # Send only ward — diagnosis and full_name must NOT be wiped.
        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"ward": "Ward B"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ward"] == "Ward B"
        assert body["full_name"] == "Original Name"
        assert body["diagnosis"] == "Original Dx"

    async def test_patch_invalid_status_returns_422(
        self, api_client: AsyncClient, db_session
    ):
        """Invalid enum value should fail Pydantic validation, not silently
        commit garbage to the DB."""
        hospital = await seed_hospital(db_session, "Validation Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("invalid-status-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"status": "Definitely Not A Real Status"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    async def test_patch_other_hospital_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Cross-hospital write attempt must NOT mutate another hospital's data."""
        hospital_a = await seed_hospital(db_session, "PA Hospital A")
        hospital_b = await seed_hospital(db_session, "PA Hospital B")
        doctor_a = await seed_user(
            db_session,
            email=unique_email("pa-doc-a"),
            role=UserRole.doctor,
            hospital_id=hospital_a.id,
        )
        patient_b, _ = await seed_admission(
            db_session,
            hospital_id=hospital_b.id,
            full_name="B Patient",
        )
        token_a = await login_for_token(api_client, doctor_a.email)

        resp = await api_client.patch(
            f"/api/v1/patients/{patient_b.id}",
            json={"diagnosis": "Hacked Diagnosis"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404


# ─── POST /patients/{id}/discharge ──────────────────────────────────────────


class TestDischargePatient:
    async def test_discharge_sets_status_and_timestamp(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Discharge Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("disch-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "Discharged"
        assert body["discharged_at"] is not None

    async def test_discharge_resolves_open_emergency_flags(
        self, api_client: AsyncClient, db_session
    ):
        """The handler explicitly auto-resolves any open EmergencyFlag rows
        on the admission. Pin the cascade so we don't regress to a stale
        sidebar badge after discharge."""
        hospital = await seed_hospital(db_session, "Cascade Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("cascade-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            primary_doctor_id=doctor.id,
        )

        # Seed two unresolved flags directly.
        flag_a = EmergencyFlag(
            admission_id=admission.id,
            flagged_by_id=doctor.id,
            reason="Pre-discharge flag A",
        )
        flag_b = EmergencyFlag(
            admission_id=admission.id,
            flagged_by_id=doctor.id,
            reason="Pre-discharge flag B",
        )
        db_session.add_all([flag_a, flag_b])
        await db_session.commit()

        token = await login_for_token(api_client, doctor.email)
        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Both flags should now be resolved.
        # Use a fresh session — the test's db_session has a stale snapshot
        # of the flags from before the API call committed in its own
        # session. A new session sees the latest committed state.
        async with AsyncSessionLocal() as fresh:
            rows = (
                await fresh.execute(
                    select(EmergencyFlag).where(
                        EmergencyFlag.admission_id == admission.id
                    )
                )
            ).scalars().all()
        assert len(rows) == 2
        assert all(f.is_resolved for f in rows)
        assert all(f.resolved_at is not None for f in rows)

    async def test_discharge_already_discharged_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Discharging a patient who has no active admission must fail —
        ``get_active_admission`` 404s when ``discharged_at IS NOT NULL``."""
        hospital = await seed_hospital(db_session, "Re-Discharge Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("re-disch-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            primary_doctor_id=doctor.id,
        )

        # Manually mark the admission discharged via the ORM, then try the API.
        admission.discharged_at = datetime.now(timezone.utc)
        admission.status = PatientStatus.discharged
        await db_session.commit()

        token = await login_for_token(api_client, doctor.email)
        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Active admission lookup fails → 404.
        assert resp.status_code == 404

    async def test_nurse_cannot_discharge_patient(
        self, api_client: AsyncClient, db_session
    ):
        """Discharge is restricted to admin/doctor — nurses are blocked."""
        hospital = await seed_hospital(db_session, "Role-Disch Hospital")
        nurse = await seed_user(
            db_session,
            email=unique_email("nurse-disch"),
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, nurse.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
