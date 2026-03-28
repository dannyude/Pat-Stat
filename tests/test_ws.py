"""Integration tests for the WebSocket /ws/patient/{patient_id} endpoint.

Uses starlette.testclient.TestClient (sync) for WS with mocked Redis and DB
so the tests are self-contained and avoid event-loop conflicts with
the pytest-asyncio suite.
"""

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import src.core.redis_client as _redis_module
from starlette.testclient import TestClient

from src.core.security import create_access_token, create_refresh_token
from src.domains.users.enums import UserRole
from src.main import app

PATIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HOSPITAL_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "66666666-7777-8888-9999-000000000000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: UserRole = UserRole.doctor, hospital_id: str | None = HOSPITAL_ID):
    user = MagicMock()
    user.id = USER_ID
    user.role = role
    user.hospital_id = hospital_id
    user.is_active = True
    return user


def _mock_db_session(user: MagicMock, family_link=None):
    """Async session mock that returns `user` on the first execute and
    optionally a FamilyPatientLink on the second (family auth check)."""
    call_count = 0

    async def _execute(_stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = user
        else:
            result.scalar_one_or_none.return_value = family_link
        return result

    session = AsyncMock()
    session.execute = _execute
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _silent_pubsub():
    """Pubsub mock whose listen() yields nothing — tests drive via ws_receiver."""

    async def _listen():
        return
        yield  # make it a generator

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.listen = _listen
    pubsub.__aenter__ = AsyncMock(return_value=pubsub)
    pubsub.__aexit__ = AsyncMock(return_value=False)

    redis = AsyncMock()
    redis.pubsub.return_value = pubsub
    return redis


def _message_pubsub(event: dict):
    """Pubsub mock that yields one message then blocks until cancelled.

    Blocking after the yield keeps redis_task alive so the client's close frame
    (not redis exhaustion) drives the teardown — avoids a race on Windows where
    cancelling an I/O-blocked task requires an I/O event to fire first.
    """

    async def _listen():
        yield {"type": "message", "data": json.dumps(event)}
        await asyncio.sleep(float("inf"))  # easily cancellable, never returns

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.listen = _listen
    pubsub.__aenter__ = AsyncMock(return_value=pubsub)
    pubsub.__aexit__ = AsyncMock(return_value=False)

    redis = AsyncMock()
    redis.pubsub.return_value = pubsub
    return redis


@contextmanager
def _ws_client(mock_session, mock_redis):
    """Context manager that patches all external dependencies and returns a
    TestClient. Patches the lifespan's get_redis/close_redis to prevent
    event-loop conflicts with the pytest-asyncio suite."""
    _redis_module._redis_pool = None  # reset module-level pool so TestClient starts fresh

    with (
        patch("src.main.get_redis", AsyncMock(return_value=mock_redis)),
        patch("src.main.close_redis", AsyncMock()),
        patch("src.api.v1.ws.get_redis", AsyncMock(return_value=mock_redis)),
        patch("src.api.v1.ws.AsyncSessionLocal", return_value=mock_session),
    ):
        with TestClient(app) as client:
            yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ws_rejects_wrong_message_type():
    """Server closes 1008 when first message is not {type:'auth'}."""
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "ping"})
            data = ws.receive()
            assert data["type"] == "websocket.close"
            assert data.get("code") == 1008


def test_ws_rejects_missing_token():
    """Server closes 1008 when auth message has no token field."""
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth"})
            data = ws.receive()
            assert data["type"] == "websocket.close"
            assert data.get("code") == 1008


def test_ws_rejects_invalid_jwt():
    """Server closes 1008 on a tampered/invalid token."""
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": "not.a.real.token"})
            data = ws.receive()
            assert data["type"] == "websocket.close"
            assert data.get("code") == 1008


def test_ws_rejects_refresh_token():
    """Server closes 1008 when a refresh token is used instead of access token."""
    refresh = create_refresh_token(USER_ID)
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": refresh})
            data = ws.receive()
            assert data["type"] == "websocket.close"
            assert data.get("code") == 1008


def test_ws_clinical_user_receives_connected_frame():
    """A doctor with a valid token receives {type:'connected'} after auth."""
    token = create_access_token(USER_ID, UserRole.doctor.value)
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": token})
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert msg["patient_id"] == PATIENT_ID


def test_ws_ping_pong():
    """Server replies {type:'pong'} to every {type:'ping'} frame."""
    token = create_access_token(USER_ID, UserRole.doctor.value)
    with _ws_client(_mock_db_session(_make_user()), _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.receive_json()  # connected
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


def test_ws_family_denied_without_link():
    """Family user with no FamilyPatientLink is rejected with 1008."""
    token = create_access_token(USER_ID, UserRole.family.value)
    session = _mock_db_session(
        _make_user(role=UserRole.family, hospital_id=None), family_link=None
    )
    with _ws_client(session, _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": token})
            data = ws.receive()
            assert data["type"] == "websocket.close"
            assert data.get("code") == 1008


def test_ws_family_connects_with_link():
    """Family user with a valid FamilyPatientLink connects successfully."""
    token = create_access_token(USER_ID, UserRole.family.value)
    session = _mock_db_session(
        _make_user(role=UserRole.family, hospital_id=None),
        family_link=MagicMock(),  # truthy = link exists
    )
    with _ws_client(session, _silent_pubsub()) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": token})
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert msg["patient_id"] == PATIENT_ID


def test_ws_forwards_redis_event_to_client():
    """Messages published to Redis are pushed to the connected WebSocket client."""
    token = create_access_token(USER_ID, UserRole.doctor.value)
    event = {"type": "status_changed", "status": "critical"}
    with _ws_client(_mock_db_session(_make_user()), _message_pubsub(event)) as client:
        with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.receive_json()  # connected
            pushed = ws.receive_json()
            assert pushed["type"] == "status_changed"
            assert pushed["status"] == "critical"
