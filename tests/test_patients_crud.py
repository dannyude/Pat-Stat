"""Integration tests for patient CRUD endpoints not covered elsewhere.

Covers: POST /patients, GET /patients/{id}, PATCH /patients/{id},
        POST /patients/{id}/discharge, GET /patients/{id}/updates
"""

import uuid

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


class TestCreatePatient:
    async def test_doctor_creates_patient_returns_201(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Create Patient Hospital")
        doctor_email = unique_email("create-patient-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Creator",
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.post(
            "/api/v1/patients",
            json={
                "full_name": "Jane Doe",
                "gender": "female",
                "ward": "Ward A",
                "bed_number": "A-01",
                "diagnosis": "Hypertension",
                "primary_doctor_id": str(doctor.id),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["full_name"] == "Jane Doe"
        assert data["ward"] == "Ward A"
        assert data["diagnosis"] == "Hypertension"
        assert data["discharged_at"] is None

    async def test_admin_creates_patient(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Admin Create Hospital")
        admin_email = unique_email("create-patient-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.post(
            "/api/v1/patients",
            json={
                "full_name": "John Smith",
                "ward": "Ward B",
                "bed_number": "B-01",
                "diagnosis": "Appendicitis",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["full_name"] == "John Smith"

    async def test_nurse_cannot_create_patient(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Nurse Create Hospital")
        nurse_email = unique_email("create-patient-nurse")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, nurse_email)

        resp = await api_client.post(
            "/api/v1/patients",
            json={
                "full_name": "Blocked Patient",
                "ward": "Ward C",
                "bed_number": "C-01",
                "diagnosis": "Flu",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestGetPatient:
    async def test_get_patient_returns_details(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Get Patient Hospital")
        doctor_email = unique_email("get-patient-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Retrieve Me",
            ward="Ward C",
            bed_number="C-01",
            diagnosis="Diabetes",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] == "Retrieve Me"
        assert data["diagnosis"] == "Diabetes"
        assert data["ward"] == "Ward C"

    async def test_get_patient_not_found_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "NotFound Hospital")
        doctor_email = unique_email("get-404-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            f"/api/v1/patients/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestUpdatePatient:
    async def test_update_ward_and_diagnosis(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Update Patient Hospital")
        doctor_email = unique_email("update-patient-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Update Target",
            ward="Old Ward",
            bed_number="O-01",
            diagnosis="Old Diagnosis",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"ward": "New Ward", "diagnosis": "New Diagnosis"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ward"] == "New Ward"
        assert data["diagnosis"] == "New Diagnosis"
        assert data["full_name"] == "Update Target"  # unchanged

    async def test_update_patient_full_name(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Update Name Hospital")
        doctor_email = unique_email("update-name-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Before Update",
            ward="Ward X",
            bed_number="X-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"full_name": "After Update"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "After Update"

    async def test_family_cannot_update_patient(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Update Forbidden Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("update-forbidden-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        family_email = unique_email("update-forbidden-family")
        await seed_user(db_session, email=family_email, role=UserRole.family)
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Protected Patient",
            ward="Ward Z",
            bed_number="Z-01",
            diagnosis="Test",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, family_email)
        resp = await api_client.patch(
            f"/api/v1/patients/{patient.id}",
            json={"ward": "Hack Ward"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestDischargePatient:
    async def test_discharge_sets_discharged_at(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Discharge Hospital")
        doctor_email = unique_email("discharge-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Discharge Me",
            ward="Ward D",
            bed_number="D-01",
            diagnosis="Recovered",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)
        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["discharged_at"] is not None
        assert data["status"] == "Discharged"

    async def test_discharge_already_discharged_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Double Discharge Hospital")
        doctor_email = unique_email("double-discharge-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Discharge Twice",
            ward="Ward E",
            bed_number="E-01",
            diagnosis="Test",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor_email)

        await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Second attempt — no active admission remains
        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_nurse_cannot_discharge_patient(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Nurse Discharge Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("nurse-discharge-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        nurse_email = unique_email("nurse-discharge-nurse")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Nurse Blocked Patient",
            ward="Ward N",
            bed_number="N-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, nurse_email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/discharge",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestListClinicalUpdates:
    async def test_list_clinical_updates_returns_posted_updates(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Clinical List Hospital")
        doctor_email = unique_email("clin-list-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Clinical List Patient",
            ward="Ward F",
            bed_number="F-01",
            diagnosis="Monitored",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor_email)

        await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"status": "Stable", "note": "Patient improving steadily."},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        updates = resp.json()
        assert len(updates) >= 1
        assert updates[0]["status"] == "Stable"
        assert updates[0]["note"] == "Patient improving steadily."

    async def test_list_clinical_updates_empty_before_any_posted(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Empty Updates Hospital")
        doctor_email = unique_email("empty-updates-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="No Updates Patient",
            ward="Ward G",
            bed_number="G-01",
            diagnosis="Admitted",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_clinical_updates_newest_first(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Ordered Updates Hospital")
        doctor_email = unique_email("ordered-updates-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Ordered Patient",
            ward="Ward H",
            bed_number="H-01",
            diagnosis="Test",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, doctor_email)

        await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"status": "Stable", "note": "First update."},
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={"status": "Getting Better", "note": "Second update."},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        updates = resp.json()
        assert len(updates) >= 2
        # Newest first: second update should be at index 0
        assert updates[0]["note"] == "Second update."
        assert updates[1]["note"] == "First update."
