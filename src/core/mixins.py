"""Shared SQLAlchemy model mixins and helpers."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import UUID


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


class UUIDPrimaryKey:
    """Reusable UUID primary-key column."""

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=new_uuid,
    )


class TimestampMixin:
    """Reusable created_at / updated_at columns."""

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
