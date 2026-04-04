"""CAPTCHA token verification for the Contact Sales form.

Supports Cloudflare Turnstile (default) and Google reCAPTCHA v3.
Verification is skipped when CAPTCHA_SECRET_KEY is not set (dev/test mode).
"""

import logging

import httpx
from fastapi import HTTPException

from src.core.config import settings

logger = logging.getLogger(__name__)

_TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_RECAPTCHA_URL = "https://www.google.com/recaptcha/api/siteverify"

_http_client = httpx.AsyncClient(timeout=5.0)


async def verify_captcha_token(token: str) -> None:
    """Verify a CAPTCHA token with the configured provider.

    Raises HTTP 422 if the token is invalid.
    Does nothing if CAPTCHA_SECRET_KEY is not configured (dev mode).
    """
    secret = settings.CAPTCHA_SECRET_KEY
    if not secret:
        logger.debug("CAPTCHA verification skipped — CAPTCHA_SECRET_KEY not set")
        return

    provider = settings.CAPTCHA_PROVIDER
    url = _TURNSTILE_URL if provider == "turnstile" else _RECAPTCHA_URL

    try:
        response = await _http_client.post(url, data={"secret": secret, "response": token})
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as exc:
        logger.error("CAPTCHA verification request failed: %s", exc)
        raise HTTPException(status_code=503, detail="CAPTCHA verification unavailable")

    if not result.get("success"):
        error_codes = result.get("error-codes", [])
        logger.warning("CAPTCHA verification failed: %s", error_codes)
        raise HTTPException(status_code=422, detail="CAPTCHA verification failed")
