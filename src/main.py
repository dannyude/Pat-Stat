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
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches and logs any unhandled exceptions that occur during request processing.
    Returns a generic 500 Internal Server Error to avoid exposing sensitive details.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url, exc_info=exc
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
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
