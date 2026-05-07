"""Tests for the ultimate-safety-net exception handler in ``src/main.py``.

The handler converts any unhandled ``Exception`` into a 500 response with
a polite, sanitised body. It is the production fallback that turns "this
should never happen" bugs into something the frontend can render gracefully.

Why a dedicated transport here
------------------------------
The default ``api_client`` fixture in ``conftest.py`` builds an
``AsyncClient`` with ``raise_app_exceptions=True`` — that flag re-raises
any exception that occurred during ASGI processing, even if the FastAPI
handler already sent a 500 response. That behaviour is useful for the
W4 propagation test (``test_programming_bug_in_dispatch_propagates``)
because it lets ``pytest.raises`` observe the bug.

But here we want to observe the *response* the user would see in
production, so we instantiate a fresh ``AsyncClient`` with
``raise_app_exceptions=False`` — making it behave like a real HTTP client
that just sees the 500.
"""

from unittest.mock import MagicMock

from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient
import pytest

from src.domains.users.enums import UserRole
from src.main import app
from tests.helpers import (
    login_for_token,
    seed_admission,
    seed_hospital,
    seed_user,
    unique_email,
)

pytestmark = pytest.mark.asyncio


async def _client_that_does_not_reraise() -> AsyncClient:
    """Build an httpx client whose transport surfaces the 500 response
    instead of re-raising the underlying exception."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


class TestGlobalExceptionHandler:
    async def test_unhandled_typeerror_returns_500_with_safe_message(
        self, db_session, monkeypatch
    ):
        """A TypeError escaping our code must reach the user as a 500
        with the polite, sanitised message — never the raw exception text."""
        # Make celery_app.send_task raise a TypeError (simulates a
        # regression in dispatch.py — same scenario as W4).
        buggy = MagicMock()
        buggy.send_task = MagicMock(
            side_effect=TypeError("INTERNAL: do not leak this string")
        )
        monkeypatch.setattr(
            "src.domains.notifications.dispatch.celery_app", buggy
        )

        hospital = await seed_hospital(db_session, "Global Handler Hospital")
        doctor_email = unique_email("global-doc")
        doctor = await seed_user(
            db_session,
            email=doctor_email,
            role=UserRole.doctor,
            full_name="Dr. Global",
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session,
            hospital_id=hospital.id,
            full_name="Global Patient",
            status="Stable",
            primary_doctor_id=doctor.id,
        )
        token = await login_for_token_via_default_client(doctor_email, db_session)

        async with await _client_that_does_not_reraise() as client:
            resp = await client.post(
                f"/api/v1/patients/{admission.patient_id}/updates",
                json={"status": "Getting Better", "note": "Global handler test"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 500
        body = resp.json()
        # The polite message is exactly what the handler returns.
        assert body["detail"] == (
            "An unexpected error occurred. Our engineering team has been notified."
        )
        # Critically, the raw exception message MUST NOT leak in the response.
        assert "INTERNAL: do not leak this string" not in resp.text
        assert "TypeError" not in resp.text

    async def test_http_exception_is_NOT_caught_by_global_handler(
        self, api_client: AsyncClient, db_session
    ):
        """``HTTPException`` has its own FastAPI handler that returns the
        intended status code (404, 403, etc.) with the developer-supplied
        ``detail`` string. The global Exception handler must NOT swallow
        these — otherwise every 404 in the codebase would become a 500
        with the polite message, hiding real product behaviour.

        We verify this by hitting an endpoint that 404s normally and
        asserting we get a 404 with the original detail, not a 500 with
        the sanitised message.
        """
        hospital = await seed_hospital(db_session, "HTTPExc Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("httpexc-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, doctor.email)

        # GET an unknown patient_id — patient_helpers raises HTTPException(404).
        resp = await api_client.get(
            "/api/v1/patients/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        # Original detail string survived — global handler did NOT swallow it.
        assert "not found" in resp.json()["detail"].lower()
        assert "engineering team" not in resp.json().get("detail", "")

    async def test_validation_error_is_NOT_caught_by_global_handler(
        self, api_client: AsyncClient, db_session
    ):
        """``RequestValidationError`` (from Pydantic) returns 422 with a
        structured ``detail`` array describing each field error. The
        global handler must NOT intercept this — the structured error is
        what frontends use to highlight invalid form fields."""
        hospital = await seed_hospital(db_session, "Validation Hospital")
        doctor = await seed_user(
            db_session,
            email=unique_email("validation-doc"),
            role=UserRole.doctor,
            hospital_id=hospital.id,
        )
        _patient, admission = await seed_admission(
            db_session, hospital_id=hospital.id, primary_doctor_id=doctor.id
        )
        token = await login_for_token(api_client, doctor.email)

        # status="WrongStatus" is not in PatientStatus enum → Pydantic 422.
        resp = await api_client.post(
            f"/api/v1/patients/{admission.patient_id}/updates",
            json={"status": "Definitely Not A Status", "note": "x"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        # Default FastAPI 422 body is a dict with `detail` as a LIST of
        # field errors — not the polite string from the global handler.
        assert isinstance(resp.json()["detail"], list)


# Helper used in the first test to log in *before* we swap the transport.
async def login_for_token_via_default_client(email: str, db_session) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await login_for_token(client, email)
