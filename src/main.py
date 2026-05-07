"""
PatStat Backend — FastAPI Application

This module is the main entry point for the PatStat FastAPI application.
It sets up the FastAPI app, configures logging, defines lifespan events (startup/shutdown),
registers middleware, includes API routers, and defines health check endpoints.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.core.config import settings
from src.core.database import init_db
from src.core.rate_limit import limiter
from src.core.redis_client import get_redis, close_redis
from src.api.v1.api_router import api_router
from src.api.v1.ws import router as ws_router
import src.models  # noqa: F401,PLC0414 — registers all ORM models with Base.metadata

# ─── Logging ──────────────────────────────────────────────────────────────────
# Configure the root logger based on the application's debug setting.
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────
# The lifespan context manager handles application startup and shutdown events.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Handles application startup and shutdown events.
    - On startup: Logs the environment, initializes the database (in dev),
      and connects to Redis.
    - On shutdown: Closes the Redis connection.
    """
    logger.info("🚀 PatStat starting up [%s]", settings.APP_ENV)

    # Warm up DB + Redis
    # In development, automatically create database tables.
    # For production, database migrations should be handled by Alembic.
    if settings.APP_ENV == "development":
        await init_db()

    await get_redis()
    logger.info("✅ Redis connected")

    yield

    logger.info("🛑 PatStat shutting down")
    await close_redis()


# ─── App ──────────────────────────────────────────────────────────────────────
# Initialize the FastAPI application with metadata and configuration.
# API docs (Swagger/ReDoc) are enabled only in debug mode for security.
app = FastAPI(
    title="PatStat API",
    description="Patient status tracking — Family, Clinical, and Admin API",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── Middleware ───────────────────────────────────────────────────────────────
# Configure Cross-Origin Resource Sharing (CORS) to allow requests from
# specified origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Add GZip compression to responses for better network performance.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(SlowAPIMiddleware)


# ─── Global exception handlers ────────────────────────────────────────────────
#
# The "ultimate safety net" for the API. Any exception that:
#   • is NOT an HTTPException (FastAPI handles those itself with the right code)
#   • is NOT a RequestValidationError (FastAPI returns a structured 422)
#   • is NOT a RateLimitExceeded (handled by SlowAPI's _rate_limit_exceeded_handler)
# falls through to here. We:
#   1. Log it WITH traceback (logger.exception, NOT logger.error — the former
#      captures exc_info automatically; the latter loses the stack).
#   2. Return a generic 500 to the client. We deliberately do NOT include
#      ``str(exc)`` in the response — the exception message can leak internal
#      details (table names, stack frames, secrets in error strings). The
#      client gets a polite, actionable message; the *real* details land in
#      our logs where the engineering team can see them.
#
# Why use lazy ``%s`` formatting in the log call instead of an f-string:
# logger.exception/error/info accept format args separately so the formatter
# only runs when the log level is actually enabled. f-strings are eager and
# pay the formatting cost even when the line would be filtered out.
@app.exception_handler(Exception)
async def global_unhandled_exception_handler(request: Request, exc: Exception):
    """
    Ultimate safety net for any bug that escapes per-route error handling.

    Logs the full exception (with traceback) for engineering follow-up and
    returns a polite, sanitised 500 response to the client. The client never
    sees the underlying exception message.
    """
    logger.exception(
        "Unhandled exception on %s %s — %s",
        request.method,
        request.url.path,
        exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "An unexpected error occurred. Our engineering team has been "
                "notified."
            )
        },
    )


# ─── Routers ──────────────────────────────────────────────────────────────────
# Include the main API router, which aggregates all version 1 API endpoints.
app.include_router(api_router, prefix="/api/v1")
app.include_router(
    ws_router
)  # WebSocket routes — no /api/v1 prefix, paths start with /ws/


# ─── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    """
    Provides a health check endpoint to verify the status of the application
    and its connection to essential services like Redis.
    """
    try:
        redis = await get_redis()
        await redis.ping()
        redis_ok = True
    except (OSError, RuntimeError):
        redis_ok = False

    return {
        "status": "ok",
        "env": settings.APP_ENV,
        "redis": "ok" if redis_ok else "unreachable",
    }


@app.get("/", tags=["Health"])
async def root():
    """
    Provides a simple root endpoint to confirm the API is running.
    """
    return {"message": "PatStat API v1.0 — /docs for API reference"}
