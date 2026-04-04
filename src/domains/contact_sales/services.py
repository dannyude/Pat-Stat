"""Business logic for Contact Sales form submissions.

Handles:
1. Persisting submissions to the database (for complaints analytics).
2. Dispatching an async email notification to the sales team.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.contact_sales.models import ContactSalesSubmission
from src.domains.contact_sales.schemas import ContactSalesCreate

logger = logging.getLogger(__name__)


async def create_contact_sales_submission(
    db: AsyncSession,
    payload: ContactSalesCreate,
) -> ContactSalesSubmission:
    """Persist a Contact Sales form submission and queue a notification email.

    Args:
        db: Active async database session.
        payload: Validated form data from the API layer.

    Returns:
        The newly created ContactSalesSubmission row.
    """
    submission = ContactSalesSubmission(
        first_name=payload.first_name,
        last_name=payload.last_name,
        work_email=payload.work_email,
        hospital_name=payload.hospital_name,
        message=payload.message,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    # Fire-and-forget: queue the email notification via Celery so the
    # HTTP response isn't blocked by SMTP round-trips.
    try:
        from src.tasks.contact_sales import send_contact_sales_email

        send_contact_sales_email.delay(
            submission_id=str(submission.id),
            first_name=submission.first_name,
            last_name=submission.last_name,
            work_email=submission.work_email,
            hospital_name=submission.hospital_name,
            message=submission.message,
        )
    except Exception:
        # Never let an email-queue failure block the API response.
        logger.exception("Failed to queue contact-sales notification email")

    return submission
