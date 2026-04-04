"""Contact Sales API — public endpoint for landing-page form submissions.

No authentication required. Rate-limited to prevent abuse.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.rate_limit import limiter
from src.domains.contact_sales.captcha import verify_captcha_token
from src.domains.contact_sales.schemas import ContactSalesAck, ContactSalesCreate
from src.domains.contact_sales.services import create_contact_sales_submission

router = APIRouter(prefix="/contact-sales", tags=["Contact Sales"])


@router.post(
    "",
    response_model=ContactSalesAck,
    status_code=201,
    summary="Submit a Contact Sales inquiry",
    description=(
        "Public endpoint — no auth required. "
        "Saves the submission to the database for analytics and "
        "sends a notification email to the sales team."
    ),
)
@limiter.limit("5/minute")
async def submit_contact_sales(
    request: Request,
    payload: ContactSalesCreate,
    db: AsyncSession = Depends(get_db),
) -> ContactSalesAck:
    """Handle a Contact Sales form submission from the landing page."""
    await verify_captcha_token(payload.captcha_token)
    await create_contact_sales_submission(db=db, payload=payload)
    return ContactSalesAck()
