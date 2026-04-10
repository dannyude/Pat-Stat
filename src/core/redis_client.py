import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from src.core.config import settings

logger = logging.getLogger(__name__)

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """
    Initializes and returns a singleton Redis connection pool.

    This function creates a Redis client instance if one doesn't exist,
    and returns the existing instance on subsequent calls.

    Returns:
        An asynchronous Redis client instance.
    """
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


async def close_redis():
    """Closes the Redis connection pool if it exists."""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None


async def cache_get(key: str) -> Optional[Any]:
    """
    Retrieves and deserializes a JSON value from the Redis cache.

    Args:
        key: The cache key to retrieve.

    Returns:
        The deserialized value, or None if the key does not exist.
    """
    redis = await get_redis()
    val = await redis.get(key)
    return json.loads(val) if val else None


async def cache_set(key: str, val: Any, ttl: int = 60):
    """
    Serializes and stores a value in the Redis cache with a TTL.

    Args:
        key: The cache key to set.
        val: The value to store (will be JSON serialized).
        ttl: The time-to-live for the key in seconds. Defaults to 60.
    """
    redis = await get_redis()
    await redis.set(key, json.dumps(val), ex=ttl)


async def store_refresh_token(user_id: str, jti: str, ttl_seconds: int):
    """
    Stores a refresh token's JTI in Redis to mark it as valid.

    Args:
        user_id: The ID of the user.
        jti: The unique identifier (JWT ID) of the refresh token.
        ttl_seconds: The time-to-live for the token's validity marker.
    """
    redis = await get_redis()
    await redis.setex(f"refresh:{user_id}:{jti}", ttl_seconds, "1")


async def revoke_refresh_token(user_id: str, jti: str):
    """
    Revokes a specific refresh token by deleting its JTI from Redis.

    Args:
        user_id: The ID of the user.
        jti: The unique identifier (JWT ID) of the refresh token to revoke.
    """
    redis = await get_redis()
    await redis.delete(f"refresh:{user_id}:{jti}")


async def is_refresh_token_valid(user_id: str, jti: str) -> bool:
    """
    Checks if a refresh token is still valid by looking for its JTI in Redis.

    Args:
        user_id: The ID of the user.
        jti: The unique identifier (JWT ID) of the refresh token.

    Returns:
        True if the token is valid (exists in Redis), False otherwise.
    """
    redis = await get_redis()
    return await redis.exists(f"refresh:{user_id}:{jti}") == 1


async def revoke_all_user_tokens(user_id: str):
    """
    Revokes all active refresh tokens for a user by deleting their keys from Redis.

    Args:
        user_id: The ID of the user whose tokens should be revoked.
    """
    redis = await get_redis()
    keys = await redis.keys(f"refresh:{user_id}:*")
    if keys:
        await redis.delete(*keys)


async def publish_patient_event(patient_id: str, event: dict) -> None:
    """Publish a real-time event to all WebSocket clients watching a patient.

    Silently swallows errors so a Redis hiccup never breaks the HTTP response.
    """
    try:
        redis = await get_redis()
        await redis.publish(
            f"patient:{patient_id}:updates",
            json.dumps(event, default=str),
        )
    except Exception:
        logger.exception("Failed to publish patient event for patient=%s", patient_id)


__all__ = [
    "get_redis",
    "close_redis",
    "cache_get",
    "cache_set",
    "store_refresh_token",
    "revoke_refresh_token",
    "is_refresh_token_valid",
    "revoke_all_user_tokens",
    "publish_patient_event",
]
