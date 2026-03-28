"""Notification tasks and helpers.

This module owns Celery tasks for outbound family notifications.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from src.core.config import settings
from src.domains.patients.models import FamilyPatientLink, NotificationLog
from src.domains.users.models import DeviceToken
from src.tasks.celery_app import celery_app
from src.tasks.providers.firebase_push import send_multicast

logger = logging.getLogger(__name__)

# [Performance]: Module-level engine singleton shared across all tasks in this
# worker process. Creating a new engine (and connection pool) per task call was
# the previous anti-pattern — it added ~100-200 ms overhead per task and could
# exhaust PostgreSQL connections under load.
_engine = create_engine(settings.DATABASE_URL_SYNC, pool_size=5, max_overflow=5)


@celery_app.task(
    max_retries=3,
    default_retry_delay=30,
    name="src.tasks.notifications.notify_family_of_update",
)
def notify_family_of_update(
    patient_id: str,
    patient_name: str,
    new_status: str,
    update_id: str,
    note_preview: str,
    author_name: str,
) -> dict:
    """Write NotificationLog rows and queue FCM push to all linked family members."""
    title = f"{patient_name} - {new_status}"
    body = note_preview[:120] + ("..." if len(note_preview) > 120 else "")

    with Session(_engine) as db:
        # [Performance]: Single JOIN replaces two sequential queries
        # (FamilyPatientLink then DeviceToken with .in_()).
        rows = (
            db.execute(
                select(FamilyPatientLink, DeviceToken)
                .outerjoin(
                    DeviceToken,
                    DeviceToken.user_id == FamilyPatientLink.family_user_id,
                )
                .where(FamilyPatientLink.patient_id == patient_id)
            )
            .unique()
            .all()
        )

        if not rows:
            logger.info("No family members linked to patient %s", patient_id)
            return {"sent": 0}

        # Write one NotificationLog per family user (deduplicated) so the
        # in-app notification bell is populated, then collect FCM tokens.
        seen_user_ids: set[str] = set()
        tokens: list[str] = []

        for link, device_token in rows:
            user_id = link.family_user_id
            if user_id not in seen_user_ids:
                seen_user_ids.add(user_id)
                db.add(
                    NotificationLog(
                        user_id=user_id,
                        patient_id=patient_id,
                        update_id=update_id,
                        title=title,
                        body=body,
                    )
                )
            if device_token is not None:
                tokens.append(device_token.token)

        db.commit()

    if not tokens:
        logger.info("No device tokens for patient %s family", patient_id)
        return {"sent": 0}

    _send_fcm_multicast.delay(
        tokens=tokens,
        title=title,
        body=body,
        data={
            "patient_id": patient_id,
            "update_id": update_id,
            "new_status": new_status,
            "type": "status_update",
            "author_name": author_name,
        },
    )
    logger.info("Queued FCM multicast for %d tokens", len(tokens))
    return {"sent": len(tokens)}


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="src.tasks.notifications.send_fcm_multicast",
)
def _send_fcm_multicast(
    self,
    tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """Send FCM MulticastMessage to a list of tokens."""
    try:
        result = send_multicast(tokens=tokens, title=title, body=body, data=data)
        logger.info(
            "FCM sent: %d success, %d failure", result["success"], result["failure"]
        )
        _cleanup_invalid_tokens(result.get("invalid_tokens", []))
        return {"success": result["success"], "failure": result["failure"]}
    except Exception as exc:
        logger.error("FCM multicast failed: %s", exc)
        raise self.retry(exc=exc)


def _cleanup_invalid_tokens(invalid_tokens: list[str]) -> None:
    """Delete device tokens that FCM reports as invalid/unregistered."""
    if not invalid_tokens:
        return
    # [Performance]: Reuses the module-level _engine.
    with Session(_engine) as db:
        db.execute(delete(DeviceToken).where(DeviceToken.token.in_(invalid_tokens)))
        db.commit()
    logger.info("Removed %d invalid device tokens", len(invalid_tokens))


@celery_app.task(name="src.tasks.notifications.cleanup_old_notifications")
def cleanup_old_notifications(retention_days: int = 30) -> dict:
    """Delete NotificationLog rows older than `retention_days` days (default 30).

    Runs daily at 02:00 UTC via Celery Beat.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with Session(_engine) as db:
        result = db.execute(
            delete(NotificationLog).where(NotificationLog.sent_at < cutoff)
        )
        db.commit()

    logger.info(
        "Deleted %d notification log entries older than %d days",
        result.rowcount,
        retention_days,
    )
    return {"deleted": result.rowcount, "retention_days": retention_days}


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="src.tasks.notifications.send_family_invite_email",
)
def send_family_invite_email(
    self,
    email: str,
    invite_link: str,
    access_code: str,
    patient_id: str,
    inviter_name: str,
) -> dict:
    """Placeholder email task for family invite links.

    Replace this with your SMTP/provider integration (SES, SendGrid, etc.).
    """
    try:
        logger.info(
            "Family invite queued for %s (patient=%s, inviter=%s): %s [code=%s]",
            email,
            patient_id,
            inviter_name,
            invite_link,
            access_code,
        )
        return {"queued": True}
    except Exception as exc:
        logger.error("Failed to queue family invite email: %s", exc)
        raise self.retry(exc=exc)


__all__ = [
    "notify_family_of_update",
    "cleanup_old_notifications",
    "send_family_invite_email",
]
