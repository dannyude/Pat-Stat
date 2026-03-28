from httpx import AsyncClient
import pytest

from src.domains.users.enums import UserRole
from tests.helpers import seed_user, unique_email

pytestmark = pytest.mark.asyncio


class TestStaff:
    async def test_staff_me(self, api_client: AsyncClient, db_session):
        email = unique_email("staff")
        await seed_user(
            session=db_session,
            email=email,
            role=UserRole.doctor,
            full_name="Dr. Pipeline",
        )

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123"},
        )
        token = login_resp.json()["access_token"]

        me_resp = await api_client.get(
            "/api/v1/staff/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == email
