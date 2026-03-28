"""Integration tests for the Shift Handover endpoints."""

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


class TestShiftHandover:
    async def test_create_and_list_shift_handover(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Handover Hospital")
        doctor_email = unique_email("ho-doc")
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
            full_name="Handover Patient",
            ward="Ward E",
            bed_number="E-01",
            diagnosis="Post-surgery recovery",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create a handover
        create_resp = await api_client.post(
            "/api/v1/shift-handovers",
            json={
                "admission_id": admission.id,
                "summary": "Patient stable. Continue IV drip.",
                "pending_actions": "Check blood work at 6 AM",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        handover = create_resp.json()
        assert handover["patient_name"] == "Handover Patient"
        assert handover["from_staff_name"] == "Dr. Handover"
        assert handover["summary"] == "Patient stable. Continue IV drip."
        handover_id = handover["id"]

        # List handovers
        list_resp = await api_client.get(
            "/api/v1/shift-handovers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_resp.status_code == 200
        handovers = list_resp.json()
        assert any(h["id"] == handover_id for h in handovers)

    async def test_get_single_shift_handover(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Single Handover Hospital")
        nurse_email = unique_email("ho-nurse")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            full_name="Nurse Night Shift",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Night Patient",
            ward="Ward F",
            bed_number="F-02",
            diagnosis="Observation",
        )

        token = await login_for_token(api_client, nurse_email)

        create_resp = await api_client.post(
            "/api/v1/shift-handovers",
            json={
                "admission_id": admission.id,
                "summary": "Quiet night. Patient slept well.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        handover_id = create_resp.json()["id"]

        get_resp = await api_client.get(
            f"/api/v1/shift-handovers/{handover_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["from_staff_name"] == "Nurse Night Shift"

    async def test_handover_not_found(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Not Found Hospital")
        email = unique_email("ho-notfound")
        await seed_user(
            db_session,
            email=email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/shift-handovers/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_family_cannot_create_handover(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("ho-family")
        await seed_user(db_session, email=email, role=UserRole.family)

        token = await login_for_token(api_client, email)
        resp = await api_client.post(
            "/api/v1/shift-handovers",
            json={"admission_id": "any", "summary": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
