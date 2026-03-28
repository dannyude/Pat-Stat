"""Notification endpoints — list, read, and count notifications."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_current_user
from src.domains.patients.models import NotificationLog
from src.domains.patients.schemas import NotificationOut
from src.domains.users.models import User

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# Endpoints


@router.get("", response_model=List[NotificationOut])
async def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the current user, newest first."""
    # [Performance]: We only fetch notifications owned by the current user.
    q = select(NotificationLog).where(NotificationLog.user_id == current_user.id)
    if unread_only:
        q = q.where(NotificationLog.is_read.is_(False))

    result = await db.execute(
        q.order_by(NotificationLog.sent_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.get("/unread-count")
async def unread_notification_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count of unread notifications — used for sidebar badge."""
    result = await db.execute(
        select(count(NotificationLog.id)).where(
            NotificationLog.user_id == current_user.id,
            NotificationLog.is_read.is_(False),
        )
    )
    return {"count": result.scalar_one()}


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    result = await db.execute(
        select(NotificationLog).where(
            NotificationLog.id == notification_id,
            NotificationLog.user_id == current_user.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        # [DB]: Flush to the database. The session auto-commits at the end of the request.
        await db.flush()

    return notification


@router.post("/read-all", status_code=200)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all of the current user's notifications as read."""
    now = datetime.now(timezone.utc)
    # [Performance/DB]: Perform a bulk update query rather than fetching all
    # notification ORM models and updating them in a Python loop.
    await db.execute(
        update(NotificationLog)
        .where(
            NotificationLog.user_id == current_user.id,
            NotificationLog.is_read.is_(False),
        )
        .values(is_read=True, read_at=now)
    )
    await db.flush()
    return {"detail": "All notifications marked as read"}


__all__ = ["router"]
