"""Integration tests for the Contact Sales endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.domains.contact_sales.models import ContactSalesSubmission

pytestmark = pytest.mark.asyncio

_VALID_PAYLOAD = {
    "first_name": "Jane",
    "last_name": "Doe",
    "work_email": "jane.doe@hospital.ng",
    "hospital_name": "Lagos University Teaching Hospital",
    "message": "We are interested in a product demo for our 200-bed facility.",
    "captcha_token": "test-token",
}

_URL = "/api/v1/contact-sales"


@pytest.fixture(autouse=True)
def bypass_captcha(monkeypatch: pytest.MonkeyPatch):
    """Skip CAPTCHA verification in all tests in this module."""
    monkeypatch.setattr(
        "src.api.v1.contact_sales.verify_captcha_token",
        AsyncMock(return_value=None),
    )


@pytest.fixture(autouse=True)
def mock_celery_task(monkeypatch: pytest.MonkeyPatch):
    """Prevent real Celery tasks from being dispatched during tests."""
    monkeypatch.setattr(
        "src.tasks.contact_sales.send_contact_sales_email.delay",
        lambda *args, **kwargs: None,
    )


class TestContactSalesSubmit:
    async def test_successful_submission_returns_201(
        self, api_client: AsyncClient, db_session
    ):
        resp = await api_client.post(_URL, json=_VALID_PAYLOAD)
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert "team will get back to you" in body["message"]

    async def test_submission_persisted_in_database(
        self, api_client: AsyncClient, db_session
    ):
        unique_email = f"persist-test-{__import__('uuid').uuid4().hex[:8]}@hospital.ng"
        payload = {**_VALID_PAYLOAD, "work_email": unique_email}
        await api_client.post(_URL, json=payload)

        result = await db_session.execute(
            select(ContactSalesSubmission).where(
                ContactSalesSubmission.work_email == unique_email
            )
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.first_name == "Jane"
        assert row.last_name == "Doe"
        assert row.hospital_name == "Lagos University Teaching Hospital"

    async def test_missing_required_field_returns_422(self, api_client: AsyncClient):
        payload = {**_VALID_PAYLOAD}
        del payload["work_email"]
        resp = await api_client.post(_URL, json=payload)
        assert resp.status_code == 422

    async def test_invalid_email_returns_422(self, api_client: AsyncClient):
        resp = await api_client.post(
            _URL, json={**_VALID_PAYLOAD, "work_email": "not-an-email"}
        )
        assert resp.status_code == 422

    async def test_message_too_short_returns_422(self, api_client: AsyncClient):
        resp = await api_client.post(
            _URL, json={**_VALID_PAYLOAD, "message": "Short"}
        )
        assert resp.status_code == 422

    async def test_captcha_failure_returns_422(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        from fastapi import HTTPException

        monkeypatch.setattr(
            "src.api.v1.contact_sales.verify_captcha_token",
            AsyncMock(side_effect=HTTPException(status_code=422, detail="CAPTCHA verification failed")),
        )
        resp = await api_client.post(_URL, json=_VALID_PAYLOAD)
        assert resp.status_code == 422
        assert "CAPTCHA" in resp.json()["detail"]
