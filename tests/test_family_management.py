"""Integration tests for family member management endpoints.

Covers: GET /family/patients/{patient_id}/members,
        DELETE /family/patients/{patient_id}/members/{family_user_id}
"""

import uuid

from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import (
    login_for_token,
    seed_admission,
    seed_family_link,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


class TestListFamilyMembers:
    async def test_list_returns_empty_when_no_links_exist(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Empty Family Hospital")
        admin_email = unique_email("family-list-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        doctor = await seed_user(
            db_session,
            email=unique_email("family-list-doctor"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Family Empty Patient",
            ward="Ward H",
            bed_number="H-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )

        token = await login_for_token(api_client, admin_email)
        resp = await api_client.get(
            f"/api/v1/family/patients/{patient.id}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_linked_family_members(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Linked Family Hospital")
        admin_email = unique_email("family-linked-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        doctor = await seed_user(
            db_session,
            email=unique_email("family-linked-doctor"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        family_email = unique_email("family-linked-member")
        family_user = await seed_user(
            db_session,
            email=family_email,
            role=UserRole.family,
            full_name="Family Viewer",
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Linked Family Patient",
            ward="Ward I",
            bed_number="I-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )

        await seed_family_link(patient.id, family_user.id, "Mother")

        token = await login_for_token(api_client, admin_email)
        resp = await api_client.get(
            f"/api/v1/family/patients/{patient.id}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) == 1
        assert members[0]["email"] == family_email
        assert members[0]["relationship_to_patient"] == "Mother"

    async def test_list_requires_admin_role(self, api_client: AsyncClient, db_session):
        hospital = await seed_hospital(db_session, "Family Auth Hospital")
        doctor_email = unique_email("family-auth-doc")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.get(
            f"/api/v1/family/patients/{uuid.uuid4()}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_nurse_cannot_list_family_members(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Nurse Family Auth Hospital")
        nurse_email = unique_email("family-nurse-auth")
        await seed_user(
            db_session,
            email=nurse_email,
            role=UserRole.nurse,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, nurse_email)

        resp = await api_client.get(
            f"/api/v1/family/patients/{uuid.uuid4()}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestUnlinkFamilyMember:
    async def test_unlink_removes_family_access(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Unlink Family Hospital")
        admin_email = unique_email("unlink-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        doctor = await seed_user(
            db_session,
            email=unique_email("unlink-doctor"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        family_user = await seed_user(
            db_session,
            email=unique_email("unlink-family"),
            role=UserRole.family,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Unlink Target Patient",
            ward="Ward J",
            bed_number="J-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )
        await seed_family_link(patient.id, family_user.id, "Brother")

        token = await login_for_token(api_client, admin_email)
        resp = await api_client.delete(
            f"/api/v1/family/patients/{patient.id}/members/{family_user.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Confirm the member is gone from the list
        members_resp = await api_client.get(
            f"/api/v1/family/patients/{patient.id}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert members_resp.json() == []

    async def test_unlink_nonexistent_link_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Unlink 404 Hospital")
        admin_email = unique_email("unlink-404-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        doctor = await seed_user(
            db_session,
            email=unique_email("unlink-404-doctor"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        patient, _admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="No Link Patient",
            ward="Ward K",
            bed_number="K-01",
            diagnosis="Stable",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.delete(
            f"/api/v1/family/patients/{patient.id}/members/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_unlink_requires_admin_role(
        self, api_client: AsyncClient, db_session
    ):
        doctor_email = unique_email("unlink-rbac-doc")
        hospital = await seed_hospital(db_session, "Unlink RBAC Hospital")
        await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor_email)

        resp = await api_client.delete(
            f"/api/v1/family/patients/{uuid.uuid4()}/members/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
