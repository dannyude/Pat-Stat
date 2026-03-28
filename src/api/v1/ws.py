"""WebSocket endpoint for real-time patient status updates.

Clients connect, send an auth handshake as the first JSON message, then
receive push events whenever a clinical update, emergency flag, or shift
handover is recorded for the patient.

Auth handshake (sent immediately after the connection opens):
    {"type": "auth", "token": "<access_token>"}

Keepalive (send every ~25 s to prevent idle disconnection):
    {"type": "ping"}  →  server replies {"type": "pong"}

Inbound push events:
    {"type": "status_changed",       ...}
    {"type": "emergency_flag_raised", ...}
    {"type": "handover_recorded",    ...}
"""

import asyncio
import inspect
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.core.redis_client import get_redis
from src.core.security import decode_token
from src.core.websockets import manager
from src.domains.patients.models import FamilyPatientLink
from src.domains.users.enums import UserRole
from src.domains.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])

# How long (seconds) the server waits for the auth handshake before closing.
_AUTH_TIMEOUT = 10


@router.websocket("/ws/patient/{patient_id}")
async def patient_ws(websocket: WebSocket, patient_id: str):
    """
    Real-time feed for a single patient.

    Architecture:
      - Two concurrent async tasks run for the lifetime of the connection:
          1. redis_listener — subscribes to the Redis pub/sub channel and
             forwards any published JSON message to the WebSocket client.
          2. ws_receiver    — reads frames from the client (ping/pong).
      - Either task exiting (disconnect, error) cancels the other and
        deregisters the connection from the in-memory ConnectionManager.
    """
    await websocket.accept()

    # ── 1. Auth handshake ────────────────────────────────────────────────────
    # Token is sent as the first message, not in the URL query string.
    # Tokens in query strings appear in server access logs and browser history.
    try:
        auth_msg = await asyncio.wait_for(
            websocket.receive_json(), timeout=_AUTH_TIMEOUT
        )
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=1008, reason="Auth timeout")
        return

    token = auth_msg.get("token") if isinstance(auth_msg, dict) else None
    if not auth_msg or auth_msg.get("type") != "auth" or not token:
        await websocket.close(code=1008, reason="Expected {type:'auth', token:'...'}")
        return

    try:
        payload = decode_token(token)
    except Exception:
        await websocket.close(code=1008, reason="Invalid token")
        return

    if payload.get("type") != "access":
        await websocket.close(code=1008, reason="Refresh tokens are not accepted")
        return

    user_id: str = payload.get("sub", "")

    # ── 2. Load user + authorise ─────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if not user:
            await websocket.close(code=1008, reason="User not found or inactive")
            return

        # Family members must have an explicit link to this patient.
        if user.role == UserRole.family:
            link = await db.execute(
                select(FamilyPatientLink).where(
                    FamilyPatientLink.family_user_id == user.id,
                    FamilyPatientLink.patient_id == patient_id,
                )
            )
            if not link.scalar_one_or_none():
                await websocket.close(code=1008, reason="Access denied")
                return

    # ── 3. Register & confirm ────────────────────────────────────────────────
    manager.connect(websocket, patient_id)
    await websocket.send_json({"type": "connected", "patient_id": patient_id})
    logger.info(
        "WS connected: user=%s patient=%s total=%d",
        user_id,
        patient_id,
        manager.total_connections(),
    )

    # ── 4. Concurrent tasks ──────────────────────────────────────────────────
    redis = await get_redis()
    channel = f"patient:{patient_id}:updates"

    async def redis_listener() -> None:
        """Forward Redis pub/sub messages to this WebSocket client."""
        # redis.pubsub() is sync for real redis clients, but some tests/mock
        # setups provide an awaitable here (e.g., AsyncMock). Normalize both.
        pubsub_ctx = redis.pubsub()
        if inspect.isawaitable(pubsub_ctx):
            pubsub_ctx = await pubsub_ctx

        async with pubsub_ctx as pubsub:
            await pubsub.subscribe(channel)
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        await websocket.send_json(data)
                    except (WebSocketDisconnect, RuntimeError):
                        return
            finally:
                await pubsub.unsubscribe(channel)

    async def ws_receiver() -> None:
        """Read client frames; reply to pings; ignore unknown messages."""
        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            pass

    redis_task = asyncio.create_task(redis_listener())
    ws_task = asyncio.create_task(ws_receiver())

    try:
        done, _ = await asyncio.wait(
            [redis_task, ws_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If Redis listener exits first (e.g., mock stream exhausted), keep
        # the WebSocket alive so client-driven ping/pong and close semantics
        # remain stable.
        if redis_task in done and ws_task not in done:
            await ws_task
    finally:
        redis_task.cancel()
        ws_task.cancel()
        # return_exceptions=True collects results without re-raising, so we
        # don't need suppress here.  Awaiting after cancel() is critical:
        # without it, tasks remain in "cancelling" state and block the ASGI
        # server from shutting down (manifests as a hang in tests).
        await asyncio.gather(redis_task, ws_task, return_exceptions=True)
        manager.disconnect(websocket, patient_id)
        logger.info(
            "WS disconnected: user=%s patient=%s remaining=%d",
            user_id,
            patient_id,
            manager.total_connections(),
        )


__all__ = ["router"]
