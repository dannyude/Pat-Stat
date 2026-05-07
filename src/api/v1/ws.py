"""WebSocket endpoint for real-time patient status updates.

Clients connect, send an auth handshake as the first JSON message, then
receive push events whenever a clinical update, emergency flag, or shift
handover is recorded for the patient.

Auth handshake (sent immediately after the connection opens):
    {
        "type": "auth",
        "token": "<access_token>",
        "last_synced_at": "<ISO-8601 UTC timestamp — optional>"
    }

If last_synced_at is supplied, the server replays any events missed since
that timestamp before switching to the live Redis feed. This closes the
gap caused by network drops (elevator effect).

Keepalive (send every ~25 s to prevent idle disconnection):
    {"type": "ping"}  →  server replies {"type": "pong"}

Inbound push events:
    {"type": "status_changed",        ...}
    {"type": "emergency_flag_raised",  ...}
    {"type": "handover_recorded",      ...}
    {"type": "missed_update",          ...}   ← catch-up replays only
    {"type": "catchup_failed",         ...}   ← catch-up hit a DB error; client should refresh
"""

import asyncio
import inspect
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import AsyncSessionLocal
from src.core.redis_client import get_redis
from src.core.security import decode_token
from src.core.websockets import manager
from src.domains.patients.models import (
    Admission,
    ClinicalUpdate,
    EmergencyFlag,
    FamilyPatientLink,
    ShiftHandover,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])

_AUTH_TIMEOUT = 10
_MAX_CATCHUP_HOURS = 24
# How often we re-check whether the JWT is still valid. 30 s is a balance
# between "close the socket promptly when the token expires" and "don't burn
# CPU on bookkeeping every second."
# Note that we can set this lower if compliance demands a tighter window.

_TOKEN_RECHECK_INTERVAL = 30


def _parse_last_synced_at(raw_ts: str | None, user_id: str) -> datetime | None:
    """Parse and cap the client-supplied last_synced_at timestamp."""
    if not raw_ts:
        return None
    try:
        parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        earliest = datetime.now(timezone.utc) - timedelta(hours=_MAX_CATCHUP_HOURS)
        return max(parsed, earliest)
    except (ValueError, AttributeError):
        logger.warning(
            "WS: unparseable last_synced_at=%r from user=%s", raw_ts, user_id
        )
        return None


async def _replay_missed_events(
    websocket: WebSocket,
    patient_id: str,
    since: datetime,
) -> None:
    """Query PostgreSQL for events missed since ``since`` and push them.

    Why we don't filter by "active admission only"
    ----------------------------------------------
    Earlier versions filtered events to ``admission.discharged_at IS NULL``.
    That created a real bug: a family member who reconnects shortly after
    their relative was discharged saw zero events because the catch-up
    couldn't find an *active* admission.

    Instead, we query events directly by ``patient_id`` (joining through
    Admission for the FK relationship). This naturally handles:

      • An ongoing admission with new events.
      • A recently-discharged admission whose discharge transition the
        family member missed.
      • A re-admission within the catch-up window — any events on
        either admission get replayed in chronological order.
    """
    async with AsyncSessionLocal() as db:
        cu_rows = await db.execute(
            select(ClinicalUpdate)
            .join(Admission, ClinicalUpdate.admission_id == Admission.id)
            .where(
                Admission.patient_id == patient_id,
                ClinicalUpdate.created_at > since,
            )
            .order_by(ClinicalUpdate.created_at.asc())
        )
        for cu in cu_rows.scalars().all():
            await websocket.send_json(
                {
                    "type": "missed_update",
                    "event_type": "status_changed",
                    "patient_id": patient_id,
                    "update_id": cu.id,
                    "status": cu.status.value,
                    "note": cu.note,
                    "created_at": cu.created_at.isoformat(),
                }
            )

        ef_rows = await db.execute(
            select(EmergencyFlag)
            .join(Admission, EmergencyFlag.admission_id == Admission.id)
            .options(selectinload(EmergencyFlag.flagged_by))
            .where(
                Admission.patient_id == patient_id,
                EmergencyFlag.created_at > since,
            )
            .order_by(EmergencyFlag.created_at.asc())
        )
        for ef in ef_rows.scalars().all():
            await websocket.send_json(
                {
                    "type": "missed_update",
                    "event_type": "emergency_flag_raised",
                    "patient_id": patient_id,
                    "flag_id": ef.id,
                    "priority": ef.priority.value,
                    "reason": ef.reason,
                    "created_at": ef.created_at.isoformat(),
                }
            )

        sh_rows = await db.execute(
            select(ShiftHandover)
            .join(Admission, ShiftHandover.admission_id == Admission.id)
            .where(
                Admission.patient_id == patient_id,
                ShiftHandover.created_at > since,
            )
            .order_by(ShiftHandover.created_at.asc())
        )
        for sh in sh_rows.scalars().all():
            await websocket.send_json(
                {
                    "type": "missed_update",
                    "event_type": "handover_recorded",
                    "patient_id": patient_id,
                    "handover_id": sh.id,
                    "summary": sh.summary,
                    "created_at": sh.created_at.isoformat(),
                }
            )


@router.websocket("/ws/patient/{patient_id}")
async def patient_ws(websocket: WebSocket, patient_id: str):
    """
    Real-time feed for a single patient.

    Architecture:
      - Subscribe to Redis BEFORE the catch-up query so no event falls
        between the gap of "query finished" and "subscription started".
      - Replay missed DB events (catch-up phase).
      - Flush any Redis messages that arrived during catch-up (drain buffer).
      - Hand off to two concurrent tasks for the live phase:
          1. redis_listener — forwards Redis pub/sub messages to the client.
          2. ws_receiver    — reads client frames (ping/pong).
      - Either task exiting cancels the other and deregisters the connection.
    """
    await websocket.accept()

    # ── 1. Auth handshake ────────────────────────────────────────────────────
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

    # Capture the JWT's expiry so we can close the socket when it elapses.
    # ``exp`` is a numeric Unix timestamp per RFC 7519. Missing / unparseable
    # exp falls back to "session ends with the access-token TTL from now",
    # which is the conservative interpretation.
    raw_exp = payload.get("exp")
    if isinstance(raw_exp, (int, float)):
        token_exp = datetime.fromtimestamp(float(raw_exp), tz=timezone.utc)
    else:
        token_exp = datetime.now(timezone.utc) + timedelta(
            minutes=30  # mirrors ACCESS_TOKEN_EXPIRE_MINUTES default
        )

    # ── 2. Parse last_synced_at ──────────────────────────────────────────────
    last_synced_at = _parse_last_synced_at(auth_msg.get("last_synced_at"), user_id)

    # ── 3. Load user + authorise ─────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if not user:
            await websocket.close(code=1008, reason="User not found or inactive")
            return

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

    # ── 4. Register & confirm ────────────────────────────────────────────────
    manager.connect(websocket, patient_id)
    await websocket.send_json({"type": "connected", "patient_id": patient_id})
    logger.info(
        "WS connected: user=%s patient=%s last_synced_at=%s total=%d",
        user_id,
        patient_id,
        last_synced_at.isoformat() if last_synced_at else "none",
        manager.total_connections(),
    )

    # ── 5. Subscribe to Redis BEFORE catch-up (closes the race window) ───────
    # buffer and _live_ready are used to hold messages that arrive during the catch-up query,
    # then flush them before going live.
    redis = await get_redis()
    channel = f"patient:{patient_id}:updates"

    # Buffer holds live messages that arrive while we're doing catch-up.
    _buffer: asyncio.Queue[dict] = asyncio.Queue()
    _live_ready = asyncio.Event()

    async def redis_listener() -> None:
        """Forward Redis pub/sub messages; buffer them during catch-up."""
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
                        if _live_ready.is_set():
                            # The light is Green! Send directly to the user.
                            await websocket.send_json(data)
                        else:
                            # The light is Red (DB is still catching up). Put it in the waiting room!
                            await _buffer.put(data)
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

    async def token_watchdog() -> None:
        """Close the socket when the access token's ``exp`` elapses.

        WebSocket auth in PatStat happens once at handshake. Without this
        watchdog a stolen-but-valid token could keep a socket open
        indefinitely as long as the connection isn't dropped — well past
        the 30 min access-token TTL. The watchdog wakes every
        ``_TOKEN_RECHECK_INTERVAL`` seconds, compares wall-clock to the
        captured ``exp``, and gracefully closes once expired.

        Frontends should reconnect with a fresh token on close-code 1008
        + reason ``"Token expired"`` — the existing reconnect logic already
        handles 1008 by re-authing.
        """
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now >= token_exp:
                    try:
                        await websocket.close(code=1008, reason="Token expired")
                    except (WebSocketDisconnect, RuntimeError):
                        pass
                    return
                # Sleep until the next check OR until token expiry, whichever
                # comes first. This gives precise close timing for tokens
                # that are about to expire imminently without polling faster
                # than necessary for long-lived ones.
                seconds_until_exp = max(1.0, (token_exp - now).total_seconds())
                await asyncio.sleep(min(_TOKEN_RECHECK_INTERVAL, seconds_until_exp))
            except asyncio.CancelledError:
                return

    redis_task = asyncio.create_task(redis_listener())
    ws_task = asyncio.create_task(ws_receiver())
    token_task = asyncio.create_task(token_watchdog())

    # Helper that cancels every background task in a single line — used by
    # all the failure paths below so they don't drift out of sync as we add
    # more concurrent tasks.
    async def _cleanup_tasks() -> None:
        for t in (redis_task, ws_task, token_task):
            t.cancel()
        await asyncio.gather(redis_task, ws_task, token_task, return_exceptions=True)

    # ── 6. Catch-up: replay missed events from PostgreSQL ────────────────────
    if last_synced_at is not None:
        try:
            await _replay_missed_events(websocket, patient_id, last_synced_at)
        except (WebSocketDisconnect, RuntimeError):
            await _cleanup_tasks()
            manager.disconnect(websocket, patient_id)
            return
        except (
            Exception
        ):  # noqa: BLE001 — catch-up failure must not crash the connection
            logger.exception(
                "WS catch-up failed: user=%s patient=%s", user_id, patient_id
            )
            try:
                await websocket.send_json(
                    {
                        "type": "catchup_failed",
                        "message": "Some events may have been missed. Please refresh.",
                    }
                )
            except (WebSocketDisconnect, RuntimeError):
                await _cleanup_tasks()
                manager.disconnect(websocket, patient_id)
                return

    # ── 7. Drain buffer then go live ─────────────────────────────────────────
    while not _buffer.empty():
        try:
            await websocket.send_json(_buffer.get_nowait())
        except (WebSocketDisconnect, RuntimeError):
            await _cleanup_tasks()
            manager.disconnect(websocket, patient_id)
            return

    _live_ready.set()

    # ── 8. Live phase ────────────────────────────────────────────────────────
    try:
        done, _ = await asyncio.wait(
            [redis_task, ws_task, token_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If only Redis exited (e.g. transient pub/sub error) but the client
        # is still up, keep going until the client disconnects or the token
        # expires — otherwise we'd disconnect a healthy session over a
        # background hiccup.
        if redis_task in done and ws_task not in done and token_task not in done:
            await asyncio.wait(
                [ws_task, token_task], return_when=asyncio.FIRST_COMPLETED
            )
    finally:
        await _cleanup_tasks()
        manager.disconnect(websocket, patient_id)
        logger.info(
            "WS disconnected: user=%s patient=%s remaining=%d",
            user_id,
            patient_id,
            manager.total_connections(),
        )


__all__ = ["router"]
