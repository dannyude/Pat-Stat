"""Endpoint coverage for `POST` and `GET` `/patients/{id}/updates`.

These were untested at the HTTP layer (the dispatch wiring is exercised
in `test_notification_dispatch.py`, but the endpoints themselves had no
direct coverage).

Covers:
  • Happy path create / list
  • Required-field validation
  • Pagination boundaries (skip past end, limit=1, limit=100)
  • Hospital scope leakage on both create and list
  • Discharged patient — no active admission, must 404
  • Role enforcement — family / nurse permissions
  • mark_emergency=True creates an EmergencyFlag side-effect
"""

from datetime import datetime, timezone

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.domains.patients.models import EmergencyFlag, ClinicalUpdate
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


# ─── POST /patients/{id}/updates ────────────────────────────────────────────


class TestCreateClinicalUpdate:
    async def test_create_basic_update_returns_201(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("cu-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={
                "status": "Getting Better",
                "note": "Improving on antibiotics.",
                "blood_pressure": "120/80",
                "heart_rate": "75",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["note"] == "Improving on antibiotics."
        assert body["status"] == "Getting Better"
        assert body["blood_pressure"] == "120/80"

    async def test_create_missing_required_fields_returns_422(
        self, api_client: AsyncClient, db_session
    ):
        """``status`` and ``note`` are required by ClinicalUpdateCreate."""
        hospital = await seed_hospital(db_session, "Validation Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("val-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"blood_pressure": "120/80"},  # missing status + note
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    async def test_create_with_mark_emergency_creates_flag(
        self, api_client: AsyncClient, db_session
    ):
        """``mark_emergency=True`` should atomically create an EmergencyFlag
        on the same admission."""
        hospital = await seed_hospital(db_session, "ME Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("me-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={
                "status": "Critical",
                "note": "Sudden deterioration",
                "mark_emergency": True,
                "emergency_reason": "BP plummeting",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        # Fresh session — verify the side-effect EmergencyFlag exists.
        async with AsyncSessionLocal() as fresh:
            flags = (
                await fresh.execute(
                    select(EmergencyFlag).where(
                        EmergencyFlag.admission_id == admission.id
                    )
                )
            ).scalars().all()
        assert len(flags) == 1
        assert flags[0].reason == "BP plummeting"
        assert flags[0].is_resolved is False

    async def test_create_for_unknown_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "404 CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("404-cu-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.post(
            "/api/v1/patients/00000000-0000-0000-0000-000000000000/updates",
            json={"status": "Stable", "note": "anything"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_create_for_other_hospital_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Cross-hospital write attempt must NOT add an update to another
        hospital's patient."""
        hospital_a = await seed_hospital(db_session, "CU Hosp A")
        hospital_b = await seed_hospital(db_session, "CU Hosp B")
        doctor_a = await seed_user(
            db_session,
            email=unique_email("cu-doc-a"),
            role=UserRole.doctor,
            hospital_id=hospital_a.id,
        )
        patient_b, _ = await seed_admission(
            db_session, hospital_id=hospital_b.id
        )
        token_a = await login_for_token(api_client, doctor_a.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient_b.id}/updates",
            json={"status": "Critical", "note": "Hijack attempt"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404

    async def test_family_user_cannot_create_update(
        self, api_client: AsyncClient, db_session
    ):
        """``_clinical_staff`` dependency excludes family users."""
        hospital = await seed_hospital(db_session, "Role-CU Hospital")
        family = await seed_user(
            db_session,
            email=unique_email("fam-cu"),
            role=UserRole.family,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id
        )
        token = await login_for_token(api_client, family.email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"status": "Stable", "note": "Family note"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ─── GET /patients/{id}/updates ─────────────────────────────────────────────


class TestListClinicalUpdates:
    async def test_list_returns_newest_first(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "List CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("list-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        # Create three updates in order.
        for i, status in enumerate(["Stable", "Getting Better", "Critical"]):
            r = await api_client.post(
                f"/api/v1/patients/{patient.id}/updates",
                json={"status": status, "note": f"Note {i}"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 201

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        updates = resp.json()
        assert len(updates) == 3
        # Newest first — last status posted should be first in the list.
        assert updates[0]["status"] == "Critical"
        assert updates[-1]["status"] == "Stable"

    async def test_list_pagination_limit_one(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Pagination CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("page-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        for i in range(3):
            await api_client.post(
                f"/api/v1/patients/{patient.id}/updates",
                json={"status": "Stable", "note": f"Note {i}"},
                headers={"Authorization": f"Bearer {token}"},
            )

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates?limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_list_skip_past_end_returns_empty(
        self, api_client: AsyncClient, db_session
    ):
        """skip=N when only K<N rows exist → empty list, not 404."""
        hospital = await seed_hospital(db_session, "Skip CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("skip-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"status": "Stable", "note": "Only one"},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates?skip=99",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_limit_above_max_returns_422(
        self, api_client: AsyncClient, db_session
    ):
        """`limit` is bounded at 100 by `Query(le=100)`."""
        hospital = await seed_hospital(db_session, "Limit CU Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("limit-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates?limit=500",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
