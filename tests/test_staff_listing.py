"""Integration tests for staff listing endpoints.

Covers: GET /staff, GET /staff/doctors, GET /staff/nurses
"""

from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_hospital, seed_user, unique_email

pytestmark = pytest.mark.asyncio


class TestListAllStaff:
    async def test_admin_can_list_all_staff(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Staff List Hospital")
        admin_email = unique_email("staff-list-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        await seed_user(
            db_session,
            email=unique_email("listed-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        await seed_user(
            db_session,
            email=unique_email("listed-nurse"),
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.get(
            "/api/v1/staff",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_admin_can_filter_staff_by_role(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Staff Filter Hospital")
        admin_email = unique_email("staff-filter-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.get(
            "/api/v1/staff?role=doctor",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        for member in resp.json():
            assert member["role"] == "doctor"

    async def test_doctor_cannot_list_all_staff(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Staff Forbidden Hospital")
        doctor_email = unique_email("staff-forbidden-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            "/api/v1/staff",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_nurse_cannot_list_all_staff(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Staff Nurse Forbidden Hospital")
        nurse_email = unique_email("staff-nurse-forbidden")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, nurse_email)

        resp = await api_client.get(
            "/api/v1/staff",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestListDoctors:
    async def test_doctor_can_list_hospital_doctors(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Doctors List Hospital")
        caller_email = unique_email("doctors-list-caller")
        await seed_user(
            db_session,
            email=caller_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        await seed_user(
            db_session,
            email=unique_email("another-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, caller_email)

        resp = await api_client.get(
            "/api/v1/staff/doctors",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        for user in resp.json():
            assert user["role"] == "doctor"

    async def test_nurse_can_list_hospital_doctors(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Nurse Lists Doctors Hospital")
        nurse_email = unique_email("nurse-lists-docs")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, nurse_email)

        resp = await api_client.get(
            "/api/v1/staff/doctors",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_doctors_scoped_to_callers_hospital(
        self, api_client: AsyncClient, db_session
    ):
        hospital_a = await seed_hospital(db_session, "Hospital A Docs")
        hospital_b = await seed_hospital(db_session, "Hospital B Docs")

        caller_email = unique_email("scoped-docs-caller")
        await seed_user(
            db_session,
            email=caller_email,
            role=UserRole.doctor,
            full_name="Dr. Caller A",
            hospital_id=hospital_a.id,
        )
        await seed_user(
            db_session,
            email=unique_email("doc-in-b"),
            role=UserRole.doctor,
            full_name="Dr. Hospital B",
            hospital_id=hospital_b.id,
        )
        token = await login_for_token(api_client, caller_email)

        resp = await api_client.get(
            "/api/v1/staff/doctors",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        names = [u["full_name"] for u in resp.json()]
        assert "Dr. Hospital B" not in names

    async def test_family_cannot_list_doctors(
        self, api_client: AsyncClient, db_session
    ):
        family_email = unique_email("family-no-docs")
        await seed_user(db_session, email=family_email, role=UserRole.family)
        token = await login_for_token(api_client, family_email)

        resp = await api_client.get(
            "/api/v1/staff/doctors",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestListNurses:
    async def test_nurse_can_list_hospital_nurses(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Nurses List Hospital")
        caller_email = unique_email("nurses-list-nurse")
        await seed_user(
            db_session,
            email=caller_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        await seed_user(
            db_session,
            email=unique_email("another-nurse"),
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, caller_email)

        resp = await api_client.get(
            "/api/v1/staff/nurses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        for user in resp.json():
            assert user["role"] == "nurse"

    async def test_admin_can_list_nurses(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Admin Lists Nurses Hospital")
        admin_email = unique_email("admin-nurses")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.get(
            "/api/v1/staff/nurses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_nurses_scoped_to_callers_hospital(
        self, api_client: AsyncClient, db_session
    ):
        hospital_a = await seed_hospital(db_session, "Hospital A Nurses")
        hospital_b = await seed_hospital(db_session, "Hospital B Nurses")

        caller_email = unique_email("scoped-nurses-caller")
        await seed_user(
            db_session,
            email=caller_email,
            role=UserRole.nurse,
            full_name="Nurse Caller A",
            hospital_id=hospital_a.id,
        )
        await seed_user(
            db_session,
            email=unique_email("nurse-in-b"),
            role=UserRole.nurse,
            full_name="Nurse Hospital B",
            hospital_id=hospital_b.id,
        )
        token = await login_for_token(api_client, caller_email)

        resp = await api_client.get(
            "/api/v1/staff/nurses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        names = [u["full_name"] for u in resp.json()]
        assert "Nurse Hospital B" not in names

    async def test_family_cannot_list_nurses(self, api_client: AsyncClient, db_session):
        family_email = unique_email("family-no-nurses")
        await seed_user(db_session, email=family_email, role=UserRole.family)
        token = await login_for_token(api_client, family_email)

        resp = await api_client.get(
            "/api/v1/staff/nurses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
