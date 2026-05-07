"""Edge-case coverage for family dashboard endpoints.

These four endpoints are read-only views family members hit constantly:

  • GET /family/me/patients
  • GET /family/me/patients/{patient_id}/overview
  • GET /family/me/patients/{patient_id}/updates
  • GET /family/me/patients/{patient_id}/mobile-dashboard

None had test coverage before. The single most important behavioural
contract here is **no cross-family leakage** — Family A must never see
Family B's patient. The tests below pin that with belt-and-braces
coverage on every endpoint.
"""

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


# ─── GET /family/me/patients ───────────────────────────────────────────────


class TestListMyFamilyPatients:
    async def test_returns_only_linked_patients(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Family List Hospital")
        family = await seed_user(
            db_session, email=unique_email("fam-list"), role=UserRole.family
        )
        # Two patients, family is only linked to one.
        my_patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="My Relative"
        )
        other_patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="Stranger Patient"
        )
        await seed_family_link(my_patient.id, family.id, "Mother")

        token = await login_for_token(api_client, family.email)
        resp = await api_client.get(
            "/api/v1/family/me/patients",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        names = [p["full_name"] for p in resp.json()]
        assert "My Relative" in names
        assert "Stranger Patient" not in names

    async def test_returns_empty_when_no_links(
        self, api_client: AsyncClient, db_session
    ):
        family = await seed_user(
            db_session, email=unique_email("fam-empty"), role=UserRole.family
        )
        token = await login_for_token(api_client, family.email)

        resp = await api_client.get(
            "/api/v1/family/me/patients",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_doctor_token_denied(
        self, api_client: AsyncClient, db_session
    ):
        """The endpoint is family-only — `_family_only = require_roles(family)`."""
        hospital = await seed_hospital(db_session, "Doc-on-Family Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("doc-trying-fam"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor.email)

        resp = await api_client.get(
            "/api/v1/family/me/patients",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ─── GET /family/me/patients/{patient_id}/overview ──────────────────────────


class TestPatientOverview:
    async def test_overview_for_linked_patient_returns_200(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Overview Hospital")
        family = await seed_user(
            db_session, email=unique_email("ov-fam"), role=UserRole.family
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="OV Patient"
        )
        await seed_family_link(patient.id, family.id, "Daughter")

        token = await login_for_token(api_client, family.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{patient.id}/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_overview_for_unlinked_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Cross-family leak check — family member should NOT be able to
        request the overview of a patient they don't have a link to,
        even by guessing the patient's UUID."""
        hospital = await seed_hospital(db_session, "Cross-Family Hospital")
        family_a = await seed_user(
            db_session, email=unique_email("ov-fam-a"), role=UserRole.family
        )
        family_b_patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="B Patient"
        )
        # Family A is NOT linked to family_b_patient.

        token_a = await login_for_token(api_client, family_a.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{family_b_patient.id}/overview",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404

    async def test_overview_for_unknown_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        family = await seed_user(
            db_session, email=unique_email("ov-fam-unknown"), role=UserRole.family
        )
        token = await login_for_token(api_client, family.email)

        resp = await api_client.get(
            "/api/v1/family/me/patients/00000000-0000-0000-0000-000000000000/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ─── GET /family/me/patients/{patient_id}/updates ───────────────────────────


class TestPatientUpdates:
    async def test_updates_for_linked_patient(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Updates Hospital")
        family = await seed_user(
            db_session, email=unique_email("upd-fam"), role=UserRole.family
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="UPD Patient"
        )
        await seed_family_link(patient.id, family.id, "Son")

        token = await login_for_token(api_client, family.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{patient.id}/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_updates_for_unlinked_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """Unlinked family must get 404 with the OWASP-uniform
        ``"Patient not found"`` message — same as /overview and
        /mobile-dashboard. Pinned by the explicit
        ``assert_family_link_or_404`` check at the top of
        ``list_family_patient_updates``.

        Why we test the *message* string here too: the message is part
        of the security contract. A future "helpful" change to
        ``"You don't have access to this patient"`` would silently
        regress the no-enumeration guarantee. This test is a tripwire.
        """
        hospital = await seed_hospital(db_session, "Updates X-Family Hospital")
        family_a = await seed_user(
            db_session, email=unique_email("upd-fam-a"), role=UserRole.family
        )
        family_b_patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="Other B Patient"
        )

        token_a = await login_for_token(api_client, family_a.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{family_b_patient.id}/updates",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404
        # Uniform message — must NOT mention "your account", "linked", etc.
        assert resp.json()["detail"] == "Patient not found"

    async def test_updates_for_unknown_patient_returns_404(
        self, api_client: AsyncClient, db_session
    ):
        """An entirely fake patient_id and a real-but-unowned patient_id
        must produce IDENTICAL responses (status + body) — that's the
        whole point of the OWASP non-disclosure pattern."""
        family = await seed_user(
            db_session, email=unique_email("upd-fam-fake"), role=UserRole.family
        )
        token = await login_for_token(api_client, family.email)

        resp = await api_client.get(
            "/api/v1/family/me/patients/00000000-0000-0000-0000-000000000000/updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Patient not found"


# ─── GET /family/me/patients/{patient_id}/mobile-dashboard ──────────────────


class TestMobileDashboard:
    async def test_mobile_dashboard_for_linked_patient(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Mobile Hospital")
        family = await seed_user(
            db_session, email=unique_email("mob-fam"), role=UserRole.family
        )
        patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="Mob Patient"
        )
        await seed_family_link(patient.id, family.id, "Spouse")

        token = await login_for_token(api_client, family.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{patient.id}/mobile-dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_mobile_dashboard_unlinked_patient_404(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Mobile X-Family Hospital")
        family_a = await seed_user(
            db_session, email=unique_email("mob-fam-a"), role=UserRole.family
        )
        family_b_patient, _ = await seed_admission(
            db_session, hospital_id=hospital.id, full_name="Mob B Patient"
        )

        token_a = await login_for_token(api_client, family_a.email)
        resp = await api_client.get(
            f"/api/v1/family/me/patients/{family_b_patient.id}/mobile-dashboard",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404
