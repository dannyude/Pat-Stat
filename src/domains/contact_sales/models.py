"""SQLAlchemy models for Contact Sales submissions."""

from sqlalchemy import Column, String, Text

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey


class ContactSalesSubmission(Base, UUIDPrimaryKey, TimestampMixin):
    """Persists every Contact Sales form submission for analytics and follow-up.

    Each row corresponds to a single form submission from the landing page.
    No authentication is required — this is a public-facing endpoint.
    """

    __tablename__ = "contact_sales_submissions"

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    work_email = Column(String(255), nullable=False, index=True)
    hospital_name = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ContactSalesSubmission(id={self.id!r}, "
            f"email={self.work_email!r}, "
            f"hospital={self.hospital_name!r})>"
        )
