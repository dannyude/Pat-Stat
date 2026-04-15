"""Notification schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationOut(BaseModel):
    """Response shape for a notification entry.

    The ``category`` field maps to the frontend notification tabs:
        - ``critical_alert`` → Critical Alerts tab
        - ``system``         → System Alerts tab
        - ``shift_log``      → Shift Logs tab
        - ``general``        → shown in all tabs (default)
    """

    id: str
    title: str
    body: str
    category: str = "general"
    is_read: bool
    sent_at: datetime
    read_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
