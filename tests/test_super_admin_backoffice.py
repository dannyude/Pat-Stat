"""Integration tests for all super-admin backoffice gap features."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.domains.hospital.models import Hospital
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from tests.helpers import unique_email, login_for_token, seed_user, seed_hospital

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────────


async def _apply_hospital(api_client: AsyncClient, name: str = "Test Hospital"):
    """Submit a hospital application and return the response payload."""
    admin_email = unique_email("apply-admin")
    resp = await api_client.post(
        "/api/v1/hospitals/apply",
        json={
            "hospital_name": name,
            "hospital_address": "1 Test St",
            "hospital_phone": "08000000000",
            "hospital_email": f"hosp-{admin_email}",
            "admin_full_name": "Dr. Apply",
            "admin_email": admin_email,
            "admin_password": "SecurePassword123",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _get_super_token(api_client: AsyncClient, db_session, prefix="sa"):
    """Seed a super admin and return (token, user)."""
    email = unique_email(prefix)
    user = await seed_user(
        db_session, email=email, role=UserRole.super_admin, full_name="Super Admin"
    )
    token = await login_for_token(api_client, email)
    return token, user


# ── Reject Hospital ─────────────────────────────────────────────────────────────


class TestRejectHospital:
    async def test_reject_pending_hospital(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Reject Target Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-reject")

        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/reject",
            json={"reason": "CAC expired", "note": "Internal: docs look fake"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "inactive"
        assert data["verification_status"] == "rejected"

    async def test_reject_already_active_fails(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Active Reject Target")
        token, _ = await _get_super_token(api_client, db_session, "sa-reject-active")

        # Approve first
        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Try to reject an active hospital
        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/reject",
            json={"reason": "Changed mind"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    async def test_reject_forbidden_for_regular_admin(
        self, api_client: AsyncClient, db_session
    ):
        admin_email = unique_email("regular-reject")
        await seed_user(db_session, email=admin_email, role=UserRole.admin)
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.post(
            "/api/v1/backoffice/hospitals/12345678-1234-5678-1234-567812345678/reject",
            json={"reason": "Nope"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ── Suspend Hospital ─────────────────────────────────────────────────────────────


class TestSuspendHospital:
    async def test_suspend_active_hospital(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Suspend Target Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-suspend")

        # Approve first
        approve_resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approve_resp.status_code == 200

        # Suspend
        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/suspend",
            json={"reason": "Payment overdue", "note": "3 months behind"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "suspended"
        assert data["verification_status"] == "suspended"

    async def test_suspend_pending_hospital_fails(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Suspend Pending Target")
        token, _ = await _get_super_token(api_client, db_session, "sa-suspend-pending")

        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/suspend",
            json={"reason": "Test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400


# ── Reinstate Hospital ───────────────────────────────────────────────────────────


class TestReinstateHospital:
    async def test_reinstate_suspended_hospital(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Reinstate Target Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-reinstate")

        # Approve → Suspend → Reinstate
        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/suspend",
            json={"reason": "Temporary"},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/reinstate",
            json={"note": "Issue resolved"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["verification_status"] == "verified"

    async def test_reinstate_pending_fails(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Reinstate Pending")
        token, _ = await _get_super_token(api_client, db_session, "sa-reinstate-pend")

        resp = await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/reinstate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400


# ── List All Hospitals ───────────────────────────────────────────────────────────


class TestListAllHospitals:
    async def test_list_all_hospitals_paginated(
        self, api_client: AsyncClient, db_session
    ):
        token, _ = await _get_super_token(api_client, db_session, "sa-list-all")

        resp = await api_client.get(
            "/api/v1/backoffice/hospitals?page=1&page_size=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "meta" in data
        assert data["meta"]["page"] == 1
        assert data["meta"]["page_size"] == 5

    async def test_list_hospitals_with_status_filter(
        self, api_client: AsyncClient, db_session
    ):
        # Seed a known pending hospital
        await _apply_hospital(api_client, "FilterTest Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-filter")

        resp = await api_client.get(
            "/api/v1/backoffice/hospitals?status=pending_verification",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["status"] == "pending_verification"

    async def test_list_hospitals_with_search(
        self, api_client: AsyncClient, db_session
    ):
        await _apply_hospital(api_client, "UniqueSearchTarget99")
        token, _ = await _get_super_token(api_client, db_session, "sa-search")

        resp = await api_client.get(
            "/api/v1/backoffice/hospitals?search=UniqueSearchTarget99",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("UniqueSearchTarget99" in item["name"] for item in data["items"])


# ── Hospital Detail ──────────────────────────────────────────────────────────────


class TestHospitalDetail:
    async def test_get_hospital_detail(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "DetailTest Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-detail")

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "DetailTest Hospital"
        assert data["admin_name"] is not None
        assert data["admin_email"] is not None
        assert data["hospital_code"] is not None

    async def test_hospital_detail_not_found(
        self, api_client: AsyncClient, db_session
    ):
        token, _ = await _get_super_token(api_client, db_session, "sa-detail-404")

        resp = await api_client.get(
            "/api/v1/backoffice/hospitals/12345678-1234-5678-1234-567812345678",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ── List Super Admins ────────────────────────────────────────────────────────────


class TestListSuperAdmins:
    async def test_list_super_admins(
        self, api_client: AsyncClient, db_session
    ):
        token, user = await _get_super_token(api_client, db_session, "sa-list")

        resp = await api_client.get(
            "/api/v1/backoffice/super-admins",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(sa["email"] == user.email for sa in data)

    async def test_list_super_admins_forbidden_for_admin(
        self, api_client: AsyncClient, db_session
    ):
        admin_email = unique_email("regular-list")
        await seed_user(db_session, email=admin_email, role=UserRole.admin)
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.get(
            "/api/v1/backoffice/super-admins",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ── Toggle Super Admin Status ────────────────────────────────────────────────────


class TestToggleSuperAdminStatus:
    async def test_deactivate_super_admin(
        self, api_client: AsyncClient, db_session
    ):
        actor_token, actor = await _get_super_token(
            api_client, db_session, "sa-toggle-actor"
        )
        # Create a target super admin
        target_email = unique_email("sa-toggle-target")
        target = await seed_user(
            db_session,
            email=target_email,
            role=UserRole.super_admin,
            full_name="Target SA",
        )

        resp = await api_client.patch(
            f"/api/v1/backoffice/super-admins/{target.id}/status",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {actor_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False

    async def test_cannot_deactivate_self(
        self, api_client: AsyncClient, db_session
    ):
        token, user = await _get_super_token(api_client, db_session, "sa-toggle-self")

        resp = await api_client.patch(
            f"/api/v1/backoffice/super-admins/{user.id}/status",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "own" in resp.json()["detail"].lower()

    async def test_reactivate_super_admin(
        self, api_client: AsyncClient, db_session
    ):
        actor_token, _ = await _get_super_token(
            api_client, db_session, "sa-reactivate-actor"
        )
        target_email = unique_email("sa-reactivate-target")
        target = await seed_user(
            db_session,
            email=target_email,
            role=UserRole.super_admin,
            full_name="Reactivate SA",
        )

        # Deactivate first
        await api_client.patch(
            f"/api/v1/backoffice/super-admins/{target.id}/status",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {actor_token}"},
        )

        # Reactivate
        resp = await api_client.patch(
            f"/api/v1/backoffice/super-admins/{target.id}/status",
            json={"is_active": True},
            headers={"Authorization": f"Bearer {actor_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True


# ── Overview Enhancements ────────────────────────────────────────────────────────


class TestOverviewEnhancements:
    async def test_overview_includes_new_this_week(
        self, api_client: AsyncClient, db_session
    ):
        # Seed a hospital application (created now, so within the week)
        await _apply_hospital(api_client, "This Week Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-overview")

        resp = await api_client.get(
            "/api/v1/backoffice/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "new_this_week" in data
        assert data["new_this_week"] >= 1


# ── Audit Log Enhancements ────────────────────────────────────────────────────────


class TestAuditLogEnhancements:
    async def test_audit_log_includes_actor_name(
        self, api_client: AsyncClient, db_session
    ):
        # Perform an action that creates an audit log entry
        hospital_data = await _apply_hospital(api_client, "Audit Actor Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-audit-name")

        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            "/api/v1/backoffice/audit-log",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # At least one entry should have actor_name set
        assert any(entry.get("actor_name") is not None for entry in data)

    async def test_audit_log_filter_by_action(
        self, api_client: AsyncClient, db_session
    ):
        hospital_data = await _apply_hospital(api_client, "Audit Filter Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-audit-filter")

        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            "/api/v1/backoffice/audit-log?action=hospital.approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for entry in data:
            assert entry["action"] == "hospital.approve"


# ── Verification Timeline ────────────────────────────────────────────────────────


class TestVerificationTimeline:
    async def test_full_lifecycle_produces_timeline(
        self, api_client: AsyncClient, db_session
    ):
        """Apply → Approve → Suspend → Reinstate should produce 4 timeline events."""
        hospital_data = await _apply_hospital(api_client, "Timeline Hospital")
        token, _ = await _get_super_token(api_client, db_session, "sa-timeline")

        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/suspend",
            json={"reason": "Test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        await api_client.post(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/reinstate",
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital_data['id']}/timeline",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        events = resp.json()
        # Should have at least: submitted, approved, suspended, reinstated
        assert len(events) >= 4
        event_types = [e["event_type"] for e in events]
        assert "Application Submitted" in event_types
        assert "Approved" in event_types
        assert "Suspended" in event_types
        assert "Reinstated" in event_types
