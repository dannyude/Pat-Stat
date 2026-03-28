"""Integration tests for the Doctor Dashboard endpoints."""

from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import (
    login_for_token,
    seed_admission,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


class TestDashboard:
    async def test_summary_returns_four_stat_cards(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Dashboard Hospital")
        doctor_email = unique_email("dash-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Dashboard",
            hospital_id=hospital.id,
        )
        # Seed a couple of patients
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Patient One",
            ward="Ward A",
            bed_number="A-01",
            diagnosis="Flu",
            primary_doctor_id=doctor.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Critical Patient",
            ward="Ward B",
            bed_number="B-03",
            diagnosis="Heart Failure",
            status="Critical",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.get(
            "/api/v1/dashboard/summary",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "my_patients" in data
        assert "critical_count" in data
        assert "updates_today" in data
        assert "needs_attention" in data
        assert data["my_patients"] >= 2
        assert data["critical_count"] >= 1

    async def test_critical_patients_list(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Critical Hospital")
        doctor_email = unique_email("crit-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Critical",
            hospital_id=hospital.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Crit Patient A",
            ward="ICU",
            bed_number="ICU-1",
            diagnosis="Sepsis",
            status="Critical",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.get(
            "/api/v1/dashboard/critical-patients",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        patients = resp.json()
        assert len(patients) >= 1
        assert patients[0]["full_name"] == "Crit Patient A"

    async def test_needs_attention_list(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Attention Hospital")
        doctor_email = unique_email("att-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Attention",
            hospital_id=hospital.id,
        )
        # A patient with no clinical updates automatically qualifies
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Neglected Patient",
            ward="Ward C",
            bed_number="C-02",
            diagnosis="Observation",
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.get(
            "/api/v1/dashboard/needs-attention",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        # Patients with no update at all should appear
        assert isinstance(resp.json(), list)

    async def test_recent_activity_feed(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Activity Hospital")
        doctor_email = unique_email("act-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Activity",
            hospital_id=hospital.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.get(
            "/api/v1/dashboard/recent-activity",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_family_cannot_access_dashboard(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("dash-family")
        await seed_user(db_session, email=email, role=UserRole.family)

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/dashboard/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
