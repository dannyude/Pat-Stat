import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.domains.hospital.models import Hospital
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from tests.helpers import unique_email, login_for_token, seed_user

pytestmark = pytest.mark.asyncio


class TestHospitalHybridFlow:
    async def test_list_pending_hospitals_super_admin_only(
        self, api_client: AsyncClient, db_session
    ):
        # Seed super admin and regular admin users
        super_email = unique_email("super-admin-list")
        regular_admin_email = unique_email("regular-admin-list")
        await seed_user(
            session=db_session,
            email=super_email,
            role=UserRole.super_admin,
            full_name="Pat-Stat HQ",
        )
        await seed_user(
            session=db_session,
            email=regular_admin_email,
            role=UserRole.admin,
            full_name="Regular Admin",
        )

        super_token = await login_for_token(api_client, super_email)
        regular_token = await login_for_token(api_client, regular_admin_email)

        # Create a pending hospital through public apply flow
        pending_admin_email = unique_email("pending-hospital-admin")
        apply_payload = {
            "hospital_name": "Pending General Hospital",
            "hospital_address": "1 Pending Way",
            "hospital_phone": "08000000000",
            "hospital_email": "pending@hospital.com",
            "admin_full_name": "Pending Admin",
            "admin_email": pending_admin_email,
            "admin_password": "SecurePassword123",
        }
        apply_resp = await api_client.post(
            "/api/v1/hospitals/apply", json=apply_payload
        )
        assert apply_resp.status_code == 201

        # Super admin can list pending hospitals with pagination/metadata
        super_resp = await api_client.get(
            "/api/v1/backoffice/hospitals/pending?page=1&page_size=10",
            headers={"Authorization": f"Bearer {super_token}"},
        )
        assert super_resp.status_code == 200
        payload = super_resp.json()
        assert "items" in payload
        assert "meta" in payload
        assert isinstance(payload["items"], list)
        assert any(h["name"] == "Pending General Hospital" for h in payload["items"])
        assert payload["meta"]["page"] == 1
        assert payload["meta"]["page_size"] == 10
        assert payload["meta"]["total_pending"] >= 1
        assert payload["meta"]["most_recent_application_at"] is not None

        # Regular admin is forbidden
        regular_resp = await api_client.get(
            "/api/v1/backoffice/hospitals/pending",
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert regular_resp.status_code == 403
        assert "super_admin" in regular_resp.json()["detail"]

    async def test_hospital_application_and_approval(
        self, api_client: AsyncClient, db_session
    ):
        # 1. Hospital Admin Applies
        admin_email = unique_email("hospital-admin")
        apply_payload = {
            "hospital_name": "Lagos Medical Center",
            "hospital_address": "123 Health Ave",
            "hospital_phone": "08012345678",
            "hospital_email": "contact@lagosmed.com",
            "admin_full_name": "Dr. Tobenna",
            "admin_email": admin_email,
            "admin_password": "SecurePassword123",
        }

        resp = await api_client.post("/api/v1/hospitals/apply", json=apply_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Lagos Medical Center"
        assert data["status"] == "pending_verification"
        hospital_id = data["id"]

        # 2. Verify User was created
        user_result = await db_session.execute(
            select(User).where(User.email == admin_email)
        )
        admin_user = user_result.scalar_one()
        assert admin_user.role == UserRole.admin
        assert str(admin_user.hospital_id) == hospital_id

        # 3. Super Admin Logs In
        super_email = unique_email("super-admin")
        super_admin = await seed_user(
            session=db_session,
            email=super_email,
            role=UserRole.super_admin,
            full_name="Pat-Stat HQ",
        )
        # Super admin should have NULL hospital_id
        assert super_admin.hospital_id is None

        token = await login_for_token(api_client, super_email)

        # 4. Super Admin Approves Hospital
        approve_resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_id}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approve_resp.status_code == 200
        approve_data = approve_resp.json()
        assert approve_data["status"] == "active"
        assert approve_data["verification_status"] == "verified"

        # 5. Verify DB Audit Trail
        hospital_result = await db_session.execute(
            select(Hospital).where(Hospital.id == hospital_id)
        )
        hospital_db = hospital_result.scalar_one()
        assert str(hospital_db.verified_by_admin_id) == str(super_admin.id)
        assert hospital_db.verified_at is not None

    async def test_approve_hospital_forbidden_for_regular_admin(
        self, api_client: AsyncClient, db_session
    ):
        # Regular admin tries to approve
        admin_email = unique_email("regular-admin")
        await seed_user(session=db_session, email=admin_email, role=UserRole.admin)
        token = await login_for_token(api_client, admin_email)

        # Fake hospital ID
        resp = await api_client.post(
            "/api/v1/backoffice/hospitals/12345678-1234-5678-1234-567812345678/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "super_admin" in resp.json()["detail"]

    async def test_backoffice_super_admin_creation_capped_at_four(
        self, api_client: AsyncClient, db_session
    ):
        actor_email = unique_email("super-admin-actor")
        await seed_user(
            session=db_session,
            email=actor_email,
            role=UserRole.super_admin,
            full_name="Actor Super Admin",
        )
        token = await login_for_token(api_client, actor_email)

        count_result = await db_session.execute(
            select(User.id).where(User.role == UserRole.super_admin)
        )
        current_super_admin_count = len(count_result.scalars().all())
        remaining_slots = max(0, 3 - current_super_admin_count)

        # Fill remaining slots up to the hard cap.
        for idx in range(1, remaining_slots + 1):
            resp = await api_client.post(
                "/api/v1/backoffice/super-admins",
                json={
                    "email": unique_email(f"super-admin-new-{idx}"),
                    "full_name": f"New Super Admin {idx}",
                    "password": "SecurePassword123",
                    "phone": "08012345678",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            assert resp.json()["role"] == "super_admin"

        blocked_resp = await api_client.post(
            "/api/v1/backoffice/super-admins",
            json={
                "email": unique_email("super-admin-over-cap"),
                "full_name": "Over Cap",
                "password": "SecurePassword123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert blocked_resp.status_code == 400
        assert "limit" in blocked_resp.json()["detail"].lower()

    async def test_backoffice_super_admin_creation_rejects_duplicate_email(
        self, api_client: AsyncClient, db_session
    ):
        actor_email = unique_email("super-admin-actor-dup")
        await seed_user(
            session=db_session,
            email=actor_email,
            role=UserRole.super_admin,
            full_name="Actor Super Admin",
        )
        duplicate_email = unique_email("existing-user")
        await seed_user(
            session=db_session,
            email=duplicate_email,
            role=UserRole.admin,
            full_name="Existing Platform User",
        )
        token = await login_for_token(api_client, actor_email)

        resp = await api_client.post(
            "/api/v1/backoffice/super-admins",
            json={
                "email": duplicate_email,
                "full_name": "Should Fail",
                "password": "SecurePassword123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "already" in resp.json()["detail"].lower()
