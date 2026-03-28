import importlib

from celery import Celery
from celery.schedules import crontab

from src.core.config import settings

celery_app = Celery(
    "patstat",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    # [Correctness]: Beat schedule lives here so the `beat` process always picks
    # it up regardless of which task modules it imports.
    beat_schedule={
        "cleanup-old-notifications": {
            "task": "src.tasks.notifications.cleanup_old_notifications",
            "schedule": crontab(hour=2, minute=0),
        }
    },
)

# Import task modules so decorators register tasks on this Celery app.
importlib.import_module("src.tasks.notifications")


__all__ = ["celery_app"]
