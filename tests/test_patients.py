from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import seed_user, login_for_token, unique_email

pytestmark = pytest.mark.asyncio


class TestPatients:
    async def test_family_list_patients_empty(
        self, api_client: AsyncClient, db_session
    ):
        # Create a family user directly (public registration is now invite-only)
        email = unique_email("patients-family")
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.family,
            full_name="Family Viewer",
        )

        token = await login_for_token(api_client, email)

        list_resp = await api_client.get(
            "/api/v1/patients",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json() == []

    async def test_note_categories_catalog_for_clinical_staff(
        self,
        api_client: AsyncClient,
        db_session,
    ):
        email = unique_email("patients-doctor")
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.doctor,
            full_name="Doctor Viewer",
        )

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/patients/note-categories",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["supports_urgent_flag"] is True
        assert [item["key"] for item in payload["categories"]] == [
            "general",
            "handover",
            "procedure",
            "consultation",
            "lab_result",
            "urgent",
        ]

    async def test_note_categories_catalog_for_family_forbidden(
        self,
        api_client: AsyncClient,
        db_session,
    ):
        email = unique_email("patients-family-catalog")
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.family,
            full_name="Family Viewer",
        )

        token = await login_for_token(api_client, email)
        resp = await api_client.get(
            "/api/v1/patients/note-categories",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
