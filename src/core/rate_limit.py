"""Rate limiting configuration for FastAPI routes."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import settings

# Global limiter used by middleware and route-level decorators.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.api_rate_limit_default],
    headers_enabled=False,
    enabled=settings.APP_ENV != "testing",
)

__all__ = ["limiter"]
