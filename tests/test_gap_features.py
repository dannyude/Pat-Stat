"""Integration tests for the new Gap-closing features (search, hospital name, emergency in update)."""

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


# Gap 2: Patient Search via ?q= parameter



class TestPatientSearch:
    async def test_search_by_name(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Search Hospital")
        doctor_email = unique_email("search-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Searcher",
            hospital_id=hospital.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Alice Johnson",
            ward="Ward B",
            bed_number="B-02",
            diagnosis="Fracture",
            primary_doctor_id=doctor.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Bob Smith",
            ward="Ward C",
            bed_number="C-01",
            diagnosis="Concussion",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Search for "Alice"
        resp = await api_client.get(
            "/api/v1/patients?q=Alice",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        results = resp.json()
        assert any(p["full_name"] == "Alice Johnson" for p in results)
        assert not any(p["full_name"] == "Bob Smith" for p in results)

    async def test_search_by_ward(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Ward Search Hospital")
        doctor_email = unique_email("wardsearch-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. WardSearch",
            hospital_id=hospital.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Ward Patient",
            ward="ICU",
            bed_number="ICU-1",
            diagnosis="Critical care",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/patients?q=ICU",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        results = resp.json()
        assert any(p["ward"] == "ICU" for p in results)

    async def test_search_by_diagnosis(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Diag Search Hospital")
        doctor_email = unique_email("diagsearch-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. DiagSearch",
            hospital_id=hospital.id,
        )
        await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Diag Patient",
            ward="Ward D",
            bed_number="D-01",
            diagnosis="Acute Appendicitis",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/patients?q=Appendicitis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        results = resp.json()
        assert any(p["diagnosis"] == "Acute Appendicitis" for p in results)

    async def test_search_no_results(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "No Results Hospital")
        doctor_email = unique_email("noresults-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. NoResults",
            hospital_id=hospital.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/patients?q=DefinitelyNoMatchXYZ987",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_without_search_still_works(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "NoSearch Hospital")
        doctor_email = unique_email("nosearch-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. NoSearch",
            hospital_id=hospital.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/patients",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# Gap 3: Hospital name in user profile



class TestHospitalNameInProfile:
    async def test_me_includes_hospital_name(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Profile Hospital")
        doctor_email = unique_email("profile-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Profile",
            hospital_id=hospital.id,
        )

        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hospital_name"] == "Profile Hospital"

    async def test_me_hospital_name_null_for_family(
        self, api_client: AsyncClient, db_session
    ):
        email = unique_email("profile-family")
        await seed_user(
            db_session,
            email=email,
            role=UserRole.family,
            full_name="Family User",
        )

        token = await login_for_token(api_client, email)

        resp = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["hospital_name"] is None


# Gap 4: Emergency flag created via mark_emergency in clinical update



class TestEmergencyFlagFromUpdate:
    async def test_clinical_update_with_mark_emergency(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "MarkEmergency Hospital")
        doctor_email = unique_email("markemerg-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. MarkEmergency",
            hospital_id=hospital.id,
        )
        patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Emergency Update Patient",
            ward="ER",
            bed_number="ER-5",
            diagnosis="Severe Trauma",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create a clinical update WITH mark_emergency=True
        update_resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={
                "status": "Critical",
                "note": "Patient vitals deteriorating rapidly.",
                "blood_pressure": "80/50",
                "heart_rate": "130",
                "temperature": "39.5",
                "oxygen_level": "88",
                "mark_emergency": True,
                "emergency_reason": "Vitals dropping, unresponsive",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert update_resp.status_code == 201
        update_data = update_resp.json()
        assert update_data["status"] == "Critical"

        # Verify an emergency flag was also created
        flags_resp = await api_client.get(
            "/api/v1/emergency-flags",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert flags_resp.status_code == 200
        flags = flags_resp.json()
        patient_flags = [f for f in flags if f["patient_id"] == patient.id]
        assert len(patient_flags) >= 1
        assert any(
            f["reason"] == "Vitals dropping, unresponsive" for f in patient_flags
        )

    async def test_clinical_update_without_emergency(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "NoEmergency Hospital")
        doctor_email = unique_email("noemerg-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. NoEmergency",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Normal Update Patient",
            ward="Ward E",
            bed_number="E-01",
            diagnosis="Mild Infection",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # Create a normal clinical update (no emergency flag)
        update_resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={
                "status": "Stable",
                "note": "Patient improving steadily.",
                "blood_pressure": "120/80",
                "heart_rate": "72",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert update_resp.status_code == 201
        assert update_resp.json()["status"] == "Stable"

    async def test_clinical_update_emergency_uses_note_as_fallback_reason(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Fallback Hospital")
        doctor_email = unique_email("fallback-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Fallback",
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Fallback Patient",
            ward="Ward F",
            bed_number="F-01",
            diagnosis="Unknown",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, doctor_email)

        # mark_emergency=True but NO emergency_reason → should use the note
        update_resp = await api_client.post(
            f"/api/v1/patients/{patient.id}/updates",
            json={
                "status": "Critical",
                "note": "Sudden cardiac arrest.",
                "mark_emergency": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert update_resp.status_code == 201

        # Check the flag was created using the note as reason
        flags_resp = await api_client.get(
            "/api/v1/emergency-flags",
            headers={"Authorization": f"Bearer {token}"},
        )
        flags = flags_resp.json()
        patient_flags = [f for f in flags if f["patient_id"] == patient.id]
        assert any(f["reason"] == "Sudden cardiac arrest." for f in patient_flags)
