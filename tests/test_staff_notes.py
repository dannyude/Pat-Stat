"""Integration tests for the Staff Notes (Clinical Notes) endpoints."""

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


class TestStaffNotes:
    async def test_create_and_list_staff_notes(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Notes Hospital")
        doctor_email = unique_email("notes-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Notes",
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Notes Patient",
            ward="Ward A",
            bed_number="A-01",
            diagnosis="Flu",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create a note
        create_resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={
                "content": "Patient responding well to treatment.",
                "category": "General",
                "is_urgent": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        note_data = create_resp.json()
        assert note_data["content"] == "Patient responding well to treatment."
        assert note_data["category"] == "General"
        assert note_data["is_urgent"] is False
        assert note_data["author_name"] == "Dr. Notes"
        assert note_data["patient_id"] == patient.id
        note_id = note_data["id"]

        # List notes
        list_resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/notes",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_resp.status_code == 200
        notes = list_resp.json()
        assert any(n["id"] == note_id for n in notes)

    async def test_create_urgent_note(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Urgent Notes Hospital")
        doctor_email = unique_email("urgent-notes-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Urgent",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Urgent Notes Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={
                "content": "Critical lab results received.",
                "category": "Urgent",
                "is_urgent": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["is_urgent"] is True
        assert resp.json()["category"] == "Urgent"

    async def test_get_note_by_id(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "GetNote Hospital")
        doctor_email = unique_email("getnote-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. GetNote",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="GetNote Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        create_resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={"content": "Specific note", "category": "Consultation"},
            headers={"Authorization": f"Bearer {token}"},
        )
        note_id = create_resp.json()["id"]

        get_resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/notes/{note_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["content"] == "Specific note"
        assert get_resp.json()["category"] == "Consultation"

    async def test_get_note_not_found(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "NotFound Hospital")
        doctor_email = unique_email("notfound-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. NotFound",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="NotFound Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/notes/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_filter_notes_by_category(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Filter Hospital")
        doctor_email = unique_email("filter-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Filter",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Filter Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create notes with different categories
        await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={"content": "General note", "category": "General"},
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={"content": "Handover note", "category": "Handover"},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Filter by Handover
        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/notes?category=Handover",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        notes = resp.json()
        assert all(n["category"] == "Handover" for n in notes)

    async def test_filter_notes_urgent_only(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "UrgentFilter Hospital")
        doctor_email = unique_email("urgentfilter-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. UrgentFilter",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="UrgentFilter Patient",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create one urgent and one non-urgent note
        await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={"content": "Non-urgent", "is_urgent": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/patients/{patient.id}/notes",
            json={"content": "Urgent!", "is_urgent": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            f"/api/v1/patients/{patient.id}/notes?urgent_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        notes = resp.json()
        assert all(n["is_urgent"] is True for n in notes)

    async def test_family_cannot_create_staff_note(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("notes-family")
        await seed_user(db_session, email=email, role=UserRole.family)

        token = await login_for_token(api_client, email)
        resp = await api_client.post(
            "/api/v1/patients/any-id/notes",
            json={"content": "should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
