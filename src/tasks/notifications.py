"""Notification tasks and helpers.

This module owns Celery tasks for outbound family notifications and
staff invitation emails.
"""

import html
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional, cast

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import CursorResult
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
    category: str = "shift_log",
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
                        category=category,
                    )
                )
            if device_token is not None:
                tokens.append(device_token.token)

        db.commit()

    if not tokens:
        logger.info("No device tokens for patient %s family", patient_id)
        return {"sent": 0}

    _send_fcm_multicast.delay(  # type: ignore[attr-defined]
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
        result = cast(
            CursorResult,
            db.execute(delete(NotificationLog).where(NotificationLog.sent_at < cutoff)),
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


# ── SMTP helpers (shared with contact_sales.py pattern) ─────────────────────

_SMTP_TIMEOUT_SECONDS = 15


def _sanitize_header(value: str) -> str:
    """Strip newlines to prevent Email Header Injection attacks.

    Why: RFC 5322 headers are newline-delimited. If an attacker injects
    "\\r\\nBcc: evil@attacker.com" into a header value, the SMTP server
    treats it as a real header. Flattening to a single line neutralises this.
    """
    return "".join(value.splitlines()).strip()


def _build_staff_invite_html(
    staff_name: str,
    role: str,
    invite_link: str,
    access_code: str,
    hospital_name: str,
    inviter_name: str,
) -> str:
    """Build an HTML email body for the staff invitation.

    The template follows the same inline-CSS approach as contact_sales.py
    because email clients (Outlook, Gmail) strip <style> blocks and ignore
    external CSS. Inline styles are the only reliable cross-client method.
    """
    safe_name = html.escape(staff_name)
    safe_role = html.escape(role.title())
    safe_hospital = html.escape(hospital_name)
    safe_inviter = html.escape(inviter_name)
    safe_link = html.escape(invite_link)
    safe_code = html.escape(access_code)

    return f"""\
    <!DOCTYPE html>
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #333333; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #0052CC; border-bottom: 2px solid #EEEEEE; padding-bottom: 10px;">
            You're Invited to Join {safe_hospital}
        </h2>

        <p>Hi {safe_name},</p>

        <p>
            <strong>{safe_inviter}</strong> has invited you to join
            <strong>{safe_hospital}</strong> on Pat-Stat as a
            <strong>{safe_role}</strong>.
        </p>

        <div style="margin: 30px 0; text-align: center;">
            <a href="{safe_link}"
               style="display: inline-block; background-color: #0052CC; color: #FFFFFF;
                      padding: 14px 32px; border-radius: 6px; text-decoration: none;
                      font-weight: 600; font-size: 16px;">
                Accept Invitation
            </a>
        </div>

        <div style="background-color: #FAFBFC; padding: 15px; border-radius: 6px;
                    border: 1px solid #EEEEEE; margin: 20px 0;">
            <p style="margin: 0 0 8px 0; font-weight: 600;">Your Access Code</p>
            <p style="margin: 0; font-size: 24px; letter-spacing: 4px; font-family: monospace;
                      color: #0052CC; font-weight: 700;">{safe_code}</p>
            <p style="margin: 8px 0 0 0; font-size: 13px; color: #6B778C;">
                You'll need this code along with the link above to verify your identity.
            </p>
        </div>

        <p style="font-size: 14px; color: #6B778C;">
            This invitation expires in 72 hours. If you didn't expect this email,
            you can safely ignore it.
        </p>

        <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #EEEEEE;
                    font-size: 12px; color: #6B778C;">
            <p style="margin: 0;">This email was securely sent by Pat-Stat.</p>
        </div>
    </body>
    </html>
    """


def _build_staff_invite_plain(
    staff_name: str,
    role: str,
    invite_link: str,
    access_code: str,
    hospital_name: str,
    inviter_name: str,
) -> str:
    """Build a plain-text fallback for the staff invitation.

    Plain-text alternative exists because some email clients (corporate
    Outlook, accessibility readers) prefer or require it. The
    multipart/alternative MIME type lets the client pick whichever
    rendering it supports.
    """
    return (
        f"You're Invited to Join {hospital_name}\n"
        f"{'=' * 44}\n\n"
        f"Hi {staff_name},\n\n"
        f"{inviter_name} has invited you to join {hospital_name} "
        f"on Pat-Stat as a {role.title()}.\n\n"
        f"Accept your invitation:\n{invite_link}\n\n"
        f"Your Access Code: {access_code}\n"
        f"You'll need this code along with the link above to "
        f"verify your identity.\n\n"
        f"This invitation expires in 72 hours. If you didn't expect "
        f"this email, you can safely ignore it.\n\n"
        "---\n"
        "This email was securely sent by Pat-Stat.\n"
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="src.tasks.notifications.send_staff_invite_email",
)
def send_staff_invite_email(
    self,
    email: str,
    staff_name: str,
    role: str,
    invite_link: str,
    access_code: str,
    hospital_name: str,
    inviter_name: str,
) -> dict:
    """Send an invitation email to a new staff member (doctor/nurse).

    Follows the same SMTP pattern established in contact_sales.py:
    - Dev-mode guard: logs instead of sending when SMTP is unconfigured
    - Header injection protection via _sanitize_header
    - Multipart email: HTML primary + plain-text fallback
    - Hard SMTP timeout to prevent frozen Celery workers
    - 3x retry with 30s backoff on transient failures
    """
    smtp_host = settings.SMTP_HOST
    smtp_port = settings.SMTP_PORT
    smtp_user = settings.SMTP_USERNAME
    smtp_pass = settings.SMTP_PASSWORD
    smtp_from = settings.SMTP_FROM_EMAIL

    # Dev-mode guard — same check as contact_sales.py so local development
    # doesn't require a real mail server.
    if not smtp_host or smtp_host == "smtp.example.com":
        logger.info(
            "[DEV] Staff invite email routed to logs.\n"
            "  To: %s (%s)\n  Role: %s\n  Hospital: %s\n"
            "  Inviter: %s\n  Link: %s\n  Code: %s",
            email, staff_name, role, hospital_name,
            inviter_name, invite_link, access_code,
        )
        return {"sent": False, "reason": "SMTP not configured (dev mode)"}

    try:
        clean_email = _sanitize_header(email)
        clean_hospital = _sanitize_header(hospital_name)

        msg = EmailMessage()
        msg["Subject"] = (
            f"[Pat-Stat] You've been invited to join {clean_hospital}"
        )
        msg["From"] = smtp_from
        msg["To"] = clean_email

        plain_body = _build_staff_invite_plain(
            staff_name, role, invite_link, access_code,
            hospital_name, inviter_name,
        )
        html_body = _build_staff_invite_html(
            staff_name, role, invite_link, access_code,
            hospital_name, inviter_name,
        )

        # Plain-text first (default), HTML as alternative.
        # RFC 2046 §5.1.4: the last part is the "preferred" version,
        # so the HTML goes second.
        msg.set_content(plain_body)
        msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(
            smtp_host, smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
        ) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info(
            "Staff invite email sent to %s (hospital=%s, role=%s)",
            email, hospital_name, role,
        )
        return {"sent": True, "recipient": email}

    except Exception as exc:
        logger.error("Failed to send staff invite email to %s: %s", email, exc)
        raise self.retry(exc=exc)


__all__ = [
    "notify_family_of_update",
    "cleanup_old_notifications",
    "send_family_invite_email",
    "send_staff_invite_email",
]
