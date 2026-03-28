from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_user, unique_email

pytestmark = pytest.mark.asyncio


class TestRBAC:
    async def test_doctor_and_nurse_cannot_create_staff(
        self, api_client: AsyncClient, db_session
    ):
        doctor_email = unique_email("rbac-doctor")
        nurse_email = unique_email("rbac-nurse")
        await seed_user(
            session=db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. No Access",
        )
        await seed_user(
            session=db_session,
            email=nurse_email,
            role=UserRole.nurse,
            full_name="Nurse No Access",
        )

        doctor_token = await login_for_token(api_client, doctor_email)
        nurse_token = await login_for_token(api_client, nurse_email)

        doctor_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": unique_email("doctor-created-staff"),
                "password": "Password123",
                "full_name": "Blocked Staff",
                "role": "doctor",
            },
            headers={"Authorization": f"Bearer {doctor_token}"},
        )
        nurse_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": unique_email("nurse-created-staff"),
                "password": "Password123",
                "full_name": "Blocked Staff",
                "role": "nurse",
            },
            headers={"Authorization": f"Bearer {nurse_token}"},
        )

        assert doctor_resp.status_code == 403
        assert nurse_resp.status_code == 403

    async def test_doctor_and_nurse_cannot_pair_family_members(
        self, api_client: AsyncClient, db_session
    ):
        doctor = await seed_user(
            session=db_session,
            email=unique_email("pair-doc"),
            role=UserRole.doctor,
            full_name="Dr Pair",
        )
        nurse = await seed_user(
            session=db_session,
            email=unique_email("pair-nurse"),
            role=UserRole.nurse,
            full_name="Nurse Pair",
        )

        doctor_token = await login_for_token(api_client, doctor.email)
        nurse_token = await login_for_token(api_client, nurse.email)

        patient_id = str(uuid.uuid4())
        payload = {
            "family_user_id": str(uuid.uuid4()),
            "relationship_to_patient": "Sister",
        }
        doctor_resp = await api_client.post(
            f"/api/v1/family/patients/{patient_id}/members",
            json=payload,
            headers={"Authorization": f"Bearer {doctor_token}"},
        )
        nurse_resp = await api_client.post(
            f"/api/v1/family/patients/{patient_id}/members",
            json=payload,
            headers={"Authorization": f"Bearer {nurse_token}"},
        )

        assert doctor_resp.status_code == 403
        assert nurse_resp.status_code == 403

    async def test_admin_can_create_staff_and_pair_family_members(
        self, api_client: AsyncClient, db_session
    ):
        admin = await seed_user(
            session=db_session,
            email=unique_email("rbac-admin"),
            role=UserRole.admin,
            full_name="Admin User",
        )
        admin_token = await login_for_token(api_client, admin.email)

        create_staff_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": unique_email("admin-created-staff"),
                "password": "Password123",
                "full_name": "Created By Admin",
                "role": "doctor",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_staff_resp.status_code == 201
        assert create_staff_resp.json()["role"] == "doctor"

        family_user_id = str(uuid.uuid4())
        patient_id = str(uuid.uuid4())
        mock_link = SimpleNamespace(relationship_to_patient="Brother")
        mock_family_user = SimpleNamespace(
            id=family_user_id,
            full_name="Family User",
            email="family@test.com",
        )

        with patch(
            "src.api.v1.family_access.link_family_member_to_patient",
            new=AsyncMock(return_value=(mock_link, mock_family_user)),
        ) as mock_pair:
            pair_resp = await api_client.post(
                f"/api/v1/family/patients/{patient_id}/members",
                json={
                    "family_user_id": family_user_id,
                    "relationship_to_patient": "Brother",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert pair_resp.status_code == 200
        assert pair_resp.json()["user_id"] == family_user_id
        mock_pair.assert_awaited_once()

    async def test_admin_can_create_other_admins(
        self, api_client: AsyncClient, db_session
    ):
        """Admin can delegate admin role to create organizational hierarchy."""
        # Create first admin directly via seed (not bootstrap, since DB is shared)
        admin1 = await seed_user(
            session=db_session,
            email=unique_email("admin1"),
            role=UserRole.admin,
            full_name="Super Admin",
        )
        admin1_token = await login_for_token(api_client, admin1.email)

        # Admin 1 creates Admin 2
        admin2_email = unique_email("admin2")
        create_admin_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": admin2_email,
                "password": "AdminPassword456",
                "full_name": "Co-Admin",
                "role": "admin",
            },
            headers={"Authorization": f"Bearer {admin1_token}"},
        )
        assert create_admin_resp.status_code == 201
        assert create_admin_resp.json()["role"] == "admin"

        # Admin 2 can now log in and create a doctor
        admin2_token = await login_for_token(
            api_client,
            admin2_email,
            password="AdminPassword456",
        )
        doctor_email = unique_email("doctor-by-admin2")
        doctor_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": doctor_email,
                "password": "DoctorPassword123",
                "full_name": "Dr. Created by Admin 2",
                "role": "doctor",
            },
            headers={"Authorization": f"Bearer {admin2_token}"},
        )
        assert doctor_resp.status_code == 201
        assert doctor_resp.json()["role"] == "doctor"

    async def test_admin_cannot_create_family_account_via_register_staff(
        self, api_client: AsyncClient, db_session
    ):
        admin = await seed_user(
            session=db_session,
            email=unique_email("admin-family-create"),
            role=UserRole.admin,
            full_name="Admin Family Creator",
        )
        admin_token = await login_for_token(api_client, admin.email)

        family_email = unique_email("family-by-admin")
        create_family_resp = await api_client.post(
            "/api/v1/auth/register-staff",
            json={
                "email": family_email,
                "password": "FamilyPassword123",
                "full_name": "Family Added By Admin",
                "role": "family",
                "phone": "0100000000",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert create_family_resp.status_code == 400
        assert "doctor, nurse, and admin" in create_family_resp.json()["detail"]
