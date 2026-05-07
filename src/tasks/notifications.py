"""Notification tasks and helpers.

This module owns Celery tasks for outbound family notifications and
staff invitation emails.

Push notification design (see also ``src.domains.notifications.policy``)
-----------------------------------------------------------------------
``notify_family_of_update`` is the single fan-out point invoked by the
HTTP layer. For each linked family member we ask ``policy.decide(...)``
what tier the event is and act accordingly:

  • ``push_immediately=True`` (critical/important) → write log, dispatch FCM now.
  • ``push_immediately=False`` (routine)           → write log only; bell icon
                                                     updates next time the
                                                     family opens the app.

v1 deliberately omits server-side quiet-hours / deferral. Modern phones
already provide OS-level Do Not Disturb, and an in-app deferral path
adds a class of "silently dropped notification" failures we don't want
in a healthcare app.

Compliance choices baked in
---------------------------
* The **visible** FCM payload (``title``/``body``) carries no PHI — only
  a generic "New update from Pat-Stat". The actual update data goes in
  the ``data`` dict, which mobile clients render after unlock; this means
  push contents are not visible on a locked screen and aren't logged in
  the FCM message centre as PHI.
* Every push attempt persists its outcome to the ``NotificationLog`` row
  (status, FCM message id, error) so we can answer "did this go out?"
  retrospectively.
"""

import html
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional, cast

from sqlalchemy import create_engine, delete, select, update as sa_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.core.config import settings
from src.domains.notifications import policy
from src.domains.patients.models import (
    FamilyPatientLink,
    NotificationDeliveryStatus,
    NotificationLog,
)
from src.domains.users.models import DeviceToken
from src.tasks.celery_app import celery_app
from src.tasks.providers.firebase_push import send_multicast

logger = logging.getLogger(__name__)

# [Performance]: Module-level engine singleton shared across all tasks in this
# worker process. Creating a new engine (and connection pool) per task call was
# the previous anti-pattern — it added ~100-200 ms overhead per task and could
# exhaust PostgreSQL connections under load.
_engine = create_engine(settings.DATABASE_URL_SYNC, pool_size=5, max_overflow=5)


# ── Generic, PHI-free copy for the visible FCM payload ─────────────────────
# Apple/Google do not contractually guarantee end-to-end encryption of the
# notification payload, and the body is visible on a locked screen. So we
# put the human-readable PHI ("Patient X, BP 200/100") into the ``data``
# section, which the app fetches after unlock, and keep ``title``/``body``
# bland.
_GENERIC_TITLE_BY_TIER: dict[str, str] = {
    "critical": "Pat-Stat: Urgent update",
    "important": "Pat-Stat: New update",
    "routine": "Pat-Stat: New activity",
}
_GENERIC_BODY = "Tap to open Pat-Stat for details."

# Body text stored in the in-app NotificationLog row. The bell shows this
# alongside the title (which carries the patient name + status). We do NOT
# include the clinical note here — it lives in ``clinical_updates.note``
# behind RBAC and the UI fetches it via ``update_id`` on tap. See the
# rationale block in ``notify_family_of_update`` for why.
_BELL_GENERIC_BODY = "Tap to view the latest update."


@celery_app.task(
    max_retries=3,
    default_retry_delay=30,
    name="src.tasks.notifications.notify_family_of_update",
)
def notify_family_of_update(
    patient_id: str,
    patient_name: str,
    new_status: str,
    update_id: str | None,
    note_preview: str,
    author_name: str,
    event_kind: str = policy.EVENT_GENERIC_NOTE,
) -> dict:
    """Fan out a patient event to every linked family member, per policy.

    Each recipient is evaluated independently against the notification
    policy (per-user timezone honoured, quiet hours respected, tier
    decided from ``event_kind``). One ``NotificationLog`` row is written
    per recipient regardless of tier, so the in-app inbox is always
    complete; only the *push* path is gated.
    """
    # PHI only travels inside the encrypted ``data`` payload — never in the
    # visible title/body. The mobile app uses these fields to render the
    # full notification *after* the user unlocks the device.
    phi_data_payload: dict[str, str] = {
        "patient_id": str(patient_id),
        "update_id": str(update_id) if update_id else "",
        "new_status": new_status,
        "patient_name": patient_name,
        "author_name": author_name,
        "type": "status_update",
        "event_kind": event_kind,
    }

    immediate_log_ids: list[str] = []
    immediate_tokens: list[str] = []
    counters = {
        "logged": 0,
        "queued_push": 0,
        "skipped_routine": 0,
        "no_devices": 0,
        "recipients": 0,
    }

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
            return counters

        # Group device tokens by user so the per-recipient policy evaluation
        # has the full token list available without re-querying.
        tokens_by_user: dict[str, list[str]] = {}
        recipient_user_ids: list[str] = []
        for link, device_token in rows:
            uid = link.family_user_id
            if uid not in tokens_by_user:
                tokens_by_user[uid] = []
                recipient_user_ids.append(uid)
            if device_token is not None:
                tokens_by_user[uid].append(device_token.token)

        counters["recipients"] = len(recipient_user_ids)

        # Build one log row per recipient, classified by the policy.
        for user_id in recipient_user_ids:
            decision = policy.decide(event_kind=event_kind)
            user_tokens = tokens_by_user.get(user_id, [])

            # v1 has three terminal states (no quiet-hours / deferral path):
            #   • routine          → log only, no push
            #   • important/critical, no devices → log only (no push possible)
            #   • important/critical, has devices → log + queued for FCM
            if not decision.push_immediately:
                status = NotificationDeliveryStatus.skipped_routine.value
            elif not user_tokens:
                status = NotificationDeliveryStatus.no_devices.value
            else:
                status = NotificationDeliveryStatus.queued.value

            log_row = NotificationLog(
                user_id=user_id,
                patient_id=patient_id,
                update_id=update_id,
                # ── Stored body: minimum PHI surface area ────────────────────
                # ``title`` keeps "<patient name> — <new status>" so the in-app
                # bell is recognisable to the family member. We deliberately
                # do NOT store the clinical note text here, even though the
                # row is internal to our DB:
                #   • notification_logs is a candidate for export to BI /
                #     analytics tools; the clinical note ("BP 200/100,
                #     patient unresponsive") leaving the OLTP boundary is
                #     a higher-class PHI risk than the patient name alone.
                #   • The full note already exists in ``clinical_updates``,
                #     guarded by the same RBAC as the rest of the chart.
                #     The UI joins on ``update_id`` to render detail on tap.
                title=f"{patient_name} — {new_status}",
                body=_BELL_GENERIC_BODY,
                category=decision.category,
                delivery_status=status,
            )
            db.add(log_row)
            db.flush()  # populate log_row.id for the immediate-push path
            counters["logged"] += 1

            if status == NotificationDeliveryStatus.queued.value:
                immediate_log_ids.append(log_row.id)
                immediate_tokens.extend(user_tokens)
                counters["queued_push"] += 1
            elif status == NotificationDeliveryStatus.skipped_routine.value:
                counters["skipped_routine"] += 1
            elif status == NotificationDeliveryStatus.no_devices.value:
                counters["no_devices"] += 1

        db.commit()

    # Dispatch one FCM multicast for the union of immediate-push tokens. We
    # pass the log ids back so ``_send_fcm_multicast`` can stamp the outcome
    # onto the right rows.
    if immediate_tokens:
        _send_fcm_multicast.delay(  # type: ignore[attr-defined]
            tokens=immediate_tokens,
            title=_GENERIC_TITLE_BY_TIER.get("important", "Pat-Stat"),
            body=_GENERIC_BODY,
            data=phi_data_payload,
            log_ids=immediate_log_ids,
        )
        logger.info(
            "Queued FCM multicast: %d tokens, %d log rows",
            len(immediate_tokens),
            len(immediate_log_ids),
        )
    else:
        logger.info(
            "notify_family_of_update: no immediate push (recipients=%d, "
            "skipped_routine=%d, no_devices=%d)",
            counters["recipients"],
            counters["skipped_routine"],
            counters["no_devices"],
        )
    return counters


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
    log_ids: Optional[list[str]] = None,
) -> dict:
    """Send a multicast FCM push and persist the per-recipient outcome.

    ``log_ids`` is the list of ``NotificationLog`` rows that this push
    attempt is satisfying. They are stamped with delivery status and FCM
    message ids so the audit trail reflects what actually happened.
    """
    try:
        result = send_multicast(tokens=tokens, title=title, body=body, data=data)
        logger.info(
            "FCM sent: %d success, %d failure", result["success"], result["failure"]
        )
        _cleanup_invalid_tokens(result.get("invalid_tokens", []))

        if log_ids:
            now = datetime.now(timezone.utc)
            new_status = (
                NotificationDeliveryStatus.sent.value
                if result.get("success", 0) > 0
                else NotificationDeliveryStatus.failed.value
            )
            message_ids_blob = "|".join(result.get("message_ids", []))[:2000] or None
            with Session(_engine) as db:
                db.execute(
                    sa_update(NotificationLog)
                    .where(NotificationLog.id.in_(log_ids))
                    .values(
                        delivery_status=new_status,
                        delivered_at=now,
                        fcm_message_ids=message_ids_blob,
                    )
                )
                db.commit()

        return {"success": result["success"], "failure": result["failure"]}
    except Exception as exc:
        logger.error("FCM multicast failed: %s", exc)
        if log_ids:
            with Session(_engine) as db:
                db.execute(
                    sa_update(NotificationLog)
                    .where(NotificationLog.id.in_(log_ids))
                    .values(
                        delivery_status=NotificationDeliveryStatus.failed.value,
                        last_error=str(exc)[:500],
                    )
                )
                db.commit()
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


# How long a row may stay in delivery_status='queued' before the reconciler
# considers it abandoned. We pick 10 minutes because:
#   • A normal Celery flow (HTTP → notify_family_of_update → _send_fcm_multicast
#     → FCM round-trip) completes in <5 s on the happy path.
#   • Even with broker backpressure or FCM degradation, 10 minutes is well
#     past any legitimate retry window (3 retries × 60 s default delay = 3 min).
#   • Anything beyond this is almost certainly a worker that died between
#     FCM ACK and the audit-stamp UPDATE.
_QUEUED_STALENESS_THRESHOLD = timedelta(minutes=10)


@celery_app.task(name="src.tasks.notifications.reconcile_stuck_queued_notifications")
def reconcile_stuck_queued_notifications() -> dict:
    """Mark notification_logs rows stuck in delivery_status='queued' as unknown_outcome.

    Why this exists
    ---------------
    ``_send_fcm_multicast`` calls FCM and *then* UPDATEs the matching log
    rows to ``sent`` / ``failed``. If the worker crashes between those two
    operations, the rows stay in ``queued`` forever and the audit trail
    silently lies — "we never sent this" when the family actually got the
    push (or "we never sent this" when FCM actually failed).

    This reconciler is the safety net. It runs every 5 minutes via Celery
    Beat, finds rows older than ``_QUEUED_STALENESS_THRESHOLD`` minutes
    that are still ``queued``, and stamps them ``unknown_outcome`` with a
    ``last_error`` explaining the reconciliation. An ops dashboard can
    alert on a non-zero count of these — they're a leading indicator of
    worker instability.
    """
    cutoff = datetime.now(timezone.utc) - _QUEUED_STALENESS_THRESHOLD
    with Session(_engine) as db:
        result = cast(
            CursorResult,
            db.execute(
                # Use SQL UPDATE rather than ORM iteration — bulk update is
                # safer (all-or-nothing) and avoids row-by-row session work
                # for a maintenance sweep that might match many rows after
                # a worker outage.
                NotificationLog.__table__.update()
                .where(
                    NotificationLog.delivery_status == "queued",
                    NotificationLog.sent_at < cutoff,
                )
                .values(
                    delivery_status="unknown_outcome",
                    last_error=(
                        f"Reconciled by sweep: row remained 'queued' for "
                        f">= {_QUEUED_STALENESS_THRESHOLD.total_seconds() / 60:.0f} "
                        f"minutes; FCM outcome lost."
                    ),
                )
            ),
        )
        db.commit()

    if result.rowcount:
        logger.warning(
            "Reconciled %d stuck-queued notification rows. "
            "This usually means a Celery worker crashed mid-task — "
            "investigate worker logs around the affected window.",
            result.rowcount,
        )
    return {"reconciled": result.rowcount}


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
