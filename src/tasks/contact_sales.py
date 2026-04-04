"""Celery tasks for Contact Sales email notifications.

When a visitor submits the Contact Sales form on the landing page, this task
sends a formatted email to the configured sales inbox so the team can follow up.
"""

import html
import logging
import smtplib
from email.message import EmailMessage

from src.core.config import settings
from src.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Hard timeout for SMTP connections to prevent frozen Celery workers
_SMTP_TIMEOUT_SECONDS = 15


def _sanitize_header(value: str) -> str:
    """Strip newlines to prevent Email Header Injection attacks."""
    return "".join(value.splitlines()).strip()


def _build_sales_email_html(
    first_name: str,
    last_name: str,
    work_email: str,
    hospital_name: str,
    message: str,
    submission_id: str,
) -> str:
    """Build an HTML email body for the sales team notification."""
    safe_fname = html.escape(first_name)
    safe_lname = html.escape(last_name)
    safe_email = html.escape(work_email)
    safe_hospital = html.escape(hospital_name)
    safe_message = html.escape(message).replace("\n", "<br />")
    safe_id = html.escape(submission_id)

    return f"""\
    <!DOCTYPE html>
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #333333; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #0052CC; border-bottom: 2px solid #EEEEEE; padding-bottom: 10px;">New Contact Sales Submission</h2>

        <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
            <tr>
                <td style="padding: 10px; font-weight: 600; width: 140px; border-bottom: 1px solid #EEEEEE;">Name</td>
                <td style="padding: 10px; border-bottom: 1px solid #EEEEEE;">{safe_fname} {safe_lname}</td>
            </tr>
            <tr style="background-color: #FAFBFC;">
                <td style="padding: 10px; font-weight: 600; border-bottom: 1px solid #EEEEEE;">Work Email</td>
                <td style="padding: 10px; border-bottom: 1px solid #EEEEEE;">
                    <a href="mailto:{safe_email}" style="color: #0052CC; text-decoration: none;">{safe_email}</a>
                </td>
            </tr>
            <tr>
                <td style="padding: 10px; font-weight: 600; border-bottom: 1px solid #EEEEEE;">Hospital</td>
                <td style="padding: 10px; border-bottom: 1px solid #EEEEEE;">{safe_hospital}</td>
            </tr>
        </table>

        <div style="margin-top: 20px; background-color: #FAFBFC; padding: 15px; border-radius: 6px; border: 1px solid #EEEEEE;">
            <p style="margin-top: 0; font-weight: 600;">Message:</p>
            <p style="margin-bottom: 0; white-space: pre-wrap;">{safe_message}</p>
        </div>

        <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #EEEEEE; font-size: 12px; color: #6B778C;">
            <p style="margin: 0;">Submission ID: <code>{safe_id}</code></p>
            <p style="margin: 4px 0 0 0;">This email was securely routed by Pat-Stat.</p>
        </div>
    </body>
    </html>
    """


def _build_sales_email_plain(
    first_name: str,
    last_name: str,
    work_email: str,
    hospital_name: str,
    message: str,
    submission_id: str,
) -> str:
    """Build a plain-text fallback for the sales team notification."""
    return (
        "New Contact Sales Submission\n"
        "============================\n\n"
        f"Name:       {first_name} {last_name}\n"
        f"Work Email: {work_email}\n"
        f"Hospital:   {hospital_name}\n\n"
        f"Message:\n{message}\n\n"
        "---\n"
        f"Submission ID: {submission_id}\n"
        "This email was securely routed by Pat-Stat.\n"
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="src.tasks.contact_sales.send_contact_sales_email",
)
def send_contact_sales_email(
    self,
    submission_id: str,
    first_name: str,
    last_name: str,
    work_email: str,
    hospital_name: str,
    message: str,
) -> dict:
    """Send a notification email to the sales team about a new form submission."""

    recipient = settings.CONTACT_SALES_EMAIL
    smtp_host = settings.SMTP_HOST
    smtp_port = settings.SMTP_PORT
    smtp_user = settings.SMTP_USERNAME
    smtp_pass = settings.SMTP_PASSWORD
    smtp_from = settings.SMTP_FROM_EMAIL

    # In development, log instead of sending if SMTP is not configured.
    if not smtp_host or smtp_host == "smtp.example.com":
        logger.info(
            "[DEV] Contact Sales email routed to logs.\n"
            "  From: %s %s <%s>\n  Hospital: %s\n  Message: %s...",
            first_name, last_name, work_email, hospital_name, message[:100]
        )
        return {"sent": False, "reason": "SMTP not configured (dev mode)"}

    try:
        # 1. Clean inputs for header injection protection
        clean_hospital = _sanitize_header(hospital_name)
        clean_email = _sanitize_header(work_email)

        # 2. Use the modern EmailMessage API
        msg = EmailMessage()
        msg["Subject"] = f"[Pat-Stat] New Sales Inquiry from {clean_hospital}"
        msg["From"] = smtp_from
        msg["To"] = recipient
        msg["Reply-To"] = clean_email

        # 3. Attach Plain Text (Default), then HTML (Alternative)
        plain_body = _build_sales_email_plain(
            first_name, last_name, work_email, hospital_name, message, submission_id
        )
        html_body = _build_sales_email_html(
            first_name, last_name, work_email, hospital_name, message, submission_id
        )

        msg.set_content(plain_body)
        msg.add_alternative(html_body, subtype="html")

        # 4. Connect with a hard timeout to prevent blocking the Celery worker
        with smtplib.SMTP(smtp_host, smtp_port, timeout=_SMTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)

            server.send_message(msg)

        logger.info("Contact Sales email sent to %s (ID: %s)", recipient, submission_id)
        return {"sent": True, "recipient": recipient}

    except Exception as exc:
        logger.error("Failed to send contact sales email (ID: %s): %s", submission_id, exc)
        raise self.retry(exc=exc)


__all__ = ["send_contact_sales_email"]
