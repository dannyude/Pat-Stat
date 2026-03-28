"""Notification schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationOut(BaseModel):
    """Response shape for a notification entry."""

    id: str
    title: str
    body: str
    is_read: bool
    sent_at: datetime
    read_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
