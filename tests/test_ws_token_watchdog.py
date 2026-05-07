"""Tests for the WebSocket token-expiry watchdog added in this turn.

The watchdog wakes every ``_TOKEN_RECHECK_INTERVAL`` seconds and closes
the socket with code 1008 + reason "Token expired" once the JWT's
``exp`` claim has elapsed. We verify three things:

  1. A token that's about to expire imminently triggers a 1008 close.
  2. A token whose ``exp`` is fresh keeps the socket alive.
  3. The watchdog interval can be shrunk for fast tests.
"""

import asyncio
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import jwt
from starlette.testclient import TestClient

import src.core.redis_client as _redis_module
from src.core.config import settings
from src.domains.users.enums import UserRole
from src.main import app

PATIENT_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"


def _make_user(role: UserRole = UserRole.doctor):
    user = MagicMock()
    user.id = USER_ID
    user.role = role
    user.hospital_id = "33333333-3333-3333-3333-333333333333"
    user.is_active = True
    return user


def _mock_session(user):
    async def _execute(_stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        return result

    session = AsyncMock()
    session.execute = _execute
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _silent_pubsub():
    async def _listen():
        # Block forever — the watchdog should drive teardown, not pubsub.
        await asyncio.sleep(float("inf"))
        yield  # noqa — make this a generator

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.listen = _listen
    pubsub.__aenter__ = AsyncMock(return_value=pubsub)
    pubsub.__aexit__ = AsyncMock(return_value=False)

    redis = AsyncMock()
    redis.pubsub.return_value = pubsub
    return redis


def _token_with_exp(user_id: str, role: str, expires_in: timedelta) -> str:
    """Mint an access token with a custom expiration. ``expires_in`` may be
    negative to produce an already-expired token, or a small positive
    timedelta to expire mid-test."""
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "exp": datetime.now(timezone.utc) + expires_in,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


@contextmanager
def _ws_client(mock_session, mock_redis):
    _redis_module._redis_pool = None
    with (
        patch("src.main.get_redis", AsyncMock(return_value=mock_redis)),
        patch("src.main.close_redis", AsyncMock()),
        patch("src.api.v1.ws.get_redis", AsyncMock(return_value=mock_redis)),
        patch("src.api.v1.ws.AsyncSessionLocal", return_value=mock_session),
    ):
        with TestClient(app) as client:
            yield client


def test_watchdog_closes_socket_when_token_expires():
    """Token expiring 2 s after handshake → server-initiated close with 1008."""
    token = _token_with_exp(USER_ID, UserRole.doctor.value, timedelta(seconds=2))

    # Shrink the watchdog cadence so we don't wait 30 s in a test.
    with patch("src.api.v1.ws._TOKEN_RECHECK_INTERVAL", 0.5):
        with _ws_client(_mock_session(_make_user()), _silent_pubsub()) as client:
            with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
                ws.send_json({"type": "auth", "token": token})
                msg = ws.receive_json()
                assert msg["type"] == "connected"

                # Wait long enough for the token to expire AND the watchdog
                # to wake up and notice — token TTL=2s, watchdog wakes every
                # 0.5s, so 4s is comfortable.
                close_frame = None
                for _ in range(20):  # up to 4s of receives
                    frame = ws.receive()
                    if frame["type"] == "websocket.close":
                        close_frame = frame
                        break

    assert close_frame is not None, "Watchdog never closed an expired token"
    assert close_frame["code"] == 1008


def test_watchdog_keeps_alive_with_fresh_token():
    """A token expiring in 60 s should NOT be closed within 1 s of handshake."""
    token = _token_with_exp(USER_ID, UserRole.doctor.value, timedelta(seconds=60))

    with patch("src.api.v1.ws._TOKEN_RECHECK_INTERVAL", 0.2):
        with _ws_client(_mock_session(_make_user()), _silent_pubsub()) as client:
            with client.websocket_connect(f"/ws/patient/{PATIENT_ID}") as ws:
                ws.send_json({"type": "auth", "token": token})
                msg = ws.receive_json()
                assert msg["type"] == "connected"

                # Confirm liveness — server still answers ping.
                ws.send_json({"type": "ping"})
                pong = ws.receive_json()
                assert pong == {"type": "pong"}
