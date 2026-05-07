"""Thin dispatcher used by HTTP handlers to fan a patient event out to family.

ARCHITECTURAL NOTE: The "Thin Dispatcher" Pattern
We deliberately keep this as a one-function module rather than a class.
The HTTP handlers should know **what** they're notifying about
(``event_kind``) and **who** the patient is, but they should have absolutely
no idea HOW the notification is delivered.

By creating this "drop-box", we decouple our FastAPI web server from Celery.
If we ever decide to fire Celery and hire a new message broker (like AWS SQS
or an Argo Event Bus) to handle notifications, we only have to rewrite this
single file. The rest of the massive FastAPI app remains completely untouched.
"""

from __future__ import annotations

import logging
import socket

from src.domains.notifications import policy
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# ─── Broker / transport error types we tolerate ────────────────────────────
# We catch only **broker / transport / network** errors here. Bugs in our
# own code (TypeError from a bad kwarg dict, AttributeError from a stale
# import, etc.) MUST propagate so they show up in development instead of
# silently breaking notifications in production.
#
# What we catch:
#   • OSError / socket.error      — TCP-level failures (DNS, connection
#                                   refused, broken pipe).
#   • redis.exceptions.RedisError — every redis-py error (broker unreachable,
#                                   command timeout, auth failure).
#   • kombu.exceptions.KombuError — Celery transport layer's umbrella base
#                                   class (covers ConnectionError,
#                                   OperationalError, ChannelError…).
#
# We deliberately DO NOT catch base ``Exception`` here. The reason is the
# inverse of the original swallow-everything design: a bug in *our* code
# (e.g. a typo in the kwargs dict) would silently 200 the HTTP request
# while no notifications ever go out — and we'd never know in production
# because the only signal would be a log line nobody reads.
try:  # pragma: no cover — import shim, behaviour identical regardless of branch
    from redis.exceptions import RedisError
except ImportError:  # redis-py not installed somehow
    RedisError = ()  # type: ignore[misc, assignment]

try:  # pragma: no cover
    from kombu.exceptions import KombuError
except ImportError:  # kombu not installed somehow
    KombuError = ()  # type: ignore[misc, assignment]

_BROKER_ERRORS: tuple[type[Exception], ...] = tuple(
    cls
    for cls in (OSError, socket.error, RedisError, KombuError)
    if isinstance(cls, type) and issubclass(cls, Exception)
)


def dispatch_family_notification(
    *,
    patient_id: str,
    patient_name: str,
    event_kind: str,
    new_status: str,
    update_id: str | None,
    note_preview: str,
    author_name: str,
) -> None:
    """Enqueue ``notify_family_of_update`` for an event on a patient.

    ARCHITECTURAL NOTE: String Names vs. Direct Imports (The Memory Diet)
    Why ``send_task`` with a string instead of a direct ``.delay()`` import?
    -----------------------------------------------------------------------
    If we imported the actual `notify_family_of_update` function here,
    FastAPI would be forced to load all of its heavy dependencies (like the
    Firebase Admin SDK and SQLAlchemy wrappers) into its own RAM.
    By using `celery_app.send_task("string.name")`, FastAPI just shouts the
    task name across the room to the Celery worker, keeping the web server
    incredibly fast, lean, and free of unnecessary dependencies.
    """
    try:
        celery_app.send_task(
            "src.tasks.notifications.notify_family_of_update",
            kwargs=dict(
                patient_id=str(patient_id),
                patient_name=patient_name,
                new_status=new_status,
                update_id=str(update_id) if update_id else None,
                note_preview=note_preview or "",
                author_name=author_name,
                event_kind=event_kind,
            ),
        )

    # ARCHITECTURAL NOTE: Narrow swallow (Graceful degradation, but not blind)
    # Clinical data is strictly more important than push notifications. If
    # the doctor saves a note while our Redis broker has a bad millisecond,
    # raising here would 500 the API and potentially roll back the save —
    # unacceptable. So broker/network failures get logged and the request
    # still returns 200.
    #
    # Programming bugs are the opposite case. A typo in the kwargs dict,
    # an AttributeError from a stale import, a stray ValueError — those
    # we WANT to see (loudly) instead of silently log-and-drop. Catching
    # the broker-error tuple, not bare Exception, gives us both behaviours.
    except _BROKER_ERRORS:
        logger.exception(
            "Failed to enqueue family notification due to broker/transport error "
            "(patient=%s, kind=%s)",
            patient_id,
            event_kind,
        )


__all__ = ["dispatch_family_notification", "policy"]
