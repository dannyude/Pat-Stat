"""Integration tests for the Emergency Flags endpoints."""

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


class TestEmergencyFlags:
    async def test_create_and_list_emergency_flag(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "EF Hospital")
        doctor_email = unique_email("ef-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Emergency",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Urgent Patient",
            ward="ER",
            bed_number="ER-1",
            diagnosis="Cardiac Arrest",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create a flag
        create_resp = await api_client.post(
            "/api/v1/emergency-flags",
            json={
                "admission_id": admission.id,
                "priority": "Critical",
                "reason": "Patient unresponsive",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        flag_data = create_resp.json()
        assert flag_data["patient_name"] == "Urgent Patient"
        assert flag_data["priority"] == "Critical"
        assert flag_data["is_resolved"] is False
        flag_id = flag_data["id"]

        # List unresolved flags
        list_resp = await api_client.get(
            "/api/v1/emergency-flags",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_resp.status_code == 200
        flags = list_resp.json()
        assert any(f["id"] == flag_id for f in flags)

    async def test_resolve_emergency_flag(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Resolve Hospital")
        doctor_email = unique_email("resolve-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Resolver",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Resolve Patient",
            ward="ICU",
            bed_number="ICU-3",
            diagnosis="Stroke",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create
        create_resp = await api_client.post(
            "/api/v1/emergency-flags",
            json={
                "admission_id": admission.id,
                "priority": "High",
                "reason": "Sudden stroke symptoms",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        flag_id = create_resp.json()["id"]

        # Resolve
        resolve_resp = await api_client.patch(
            f"/api/v1/emergency-flags/{flag_id}/resolve",
            json={"resolution_note": "Patient stabilized"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["is_resolved"] is True
        assert resolve_resp.json()["resolved_by_name"] == "Dr. Resolver"

        # Resolve again should fail
        double_resolve = await api_client.patch(
            f"/api/v1/emergency-flags/{flag_id}/resolve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert double_resolve.status_code == 400

    async def test_emergency_flag_count(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Count Hospital")
        doctor_email = unique_email("count-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Counter",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Count Patient",
            ward="Ward D",
            bed_number="D-01",
            diagnosis="Pneumonia",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Count before any flags
        count_resp = await api_client.get(
            "/api/v1/emergency-flags/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert count_resp.status_code == 200
        initial_count = count_resp.json()["count"]

        # Create a flag then check count increased
        await api_client.post(
            "/api/v1/emergency-flags",
            json={
                "admission_id": admission.id,
                "priority": "High",
                "reason": "Vitals dropping",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        count_resp2 = await api_client.get(
            "/api/v1/emergency-flags/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert count_resp2.json()["count"] == initial_count + 1

    async def test_family_cannot_create_emergency_flag(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("ef-family")
        await seed_user(db_session, email=email, role=UserRole.family)

        token = await login_for_token(api_client, email)
        resp = await api_client.post(
            "/api/v1/emergency-flags",
            json={"admission_id": "any", "reason": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
