"""Firebase push provider for task orchestration code.

This module owns Firebase Admin SDK initialization and multicast sending.
"""

import logging
import os

from src.core.config import settings

logger = logging.getLogger(__name__)

_firebase_initialized = False


def _ensure_firebase_initialized() -> bool:
    if _firebase_initialized:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred_path = settings.FIREBASE_CREDENTIALS_PATH
        if not os.path.exists(cred_path):
            logger.warning(
                "Firebase credentials not found at %s - FCM disabled", cred_path
            )
            return False

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        globals()["_firebase_initialized"] = True
        logger.info("Firebase initialized")
        return True
    except (ValueError, OSError, RuntimeError) as exc:
        logger.error("Firebase init failed: %s", exc)
        return False


def send_multicast(
    tokens: list[str], title: str, body: str, data: dict | None = None
) -> dict:
    """Send a multicast FCM notification and return summary metadata."""
    if not _ensure_firebase_initialized():
        return {"success": 0, "failure": 0, "invalid_tokens": []}

    try:
        import firebase_admin
        from firebase_admin import messaging

        try:
            firebase_admin.get_app()
        except ValueError:
            logger.warning("Firebase not initialized - skipping FCM send")
            return {"success": 0, "failure": 0, "invalid_tokens": []}

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    icon="ic_notification",
                    color="#1A949D",
                    sound="default",
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)
                )
            ),
            data={k: str(v) for k, v in (data or {}).items()},
        )

        response = messaging.send_each_for_multicast(message)
        invalid_tokens = [
            tokens[idx]
            for idx, resp in enumerate(response.responses)
            if (not resp.success)
            and resp.exception
            and "UNREGISTERED" in str(resp.exception).upper()
        ]

        return {
            "success": response.success_count,
            "failure": response.failure_count,
            "invalid_tokens": invalid_tokens,
        }
    except Exception as exc:
        logger.error("FCM multicast failed: %s", exc)
        raise


__all__ = ["send_multicast"]
