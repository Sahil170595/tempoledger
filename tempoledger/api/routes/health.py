"""Health check endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Request

from tempoledger.api.middleware.rate_limit import limiter
from tempoledger.services.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


async def check_database() -> dict[str, Any]:
    """Check database connectivity.

    Returns:
        Dictionary with database health status
    """
    try:
        if db.pool is None:
            return {"status": "unhealthy", "error": "Database not connected"}

        # Try a simple query
        await db.fetchval("SELECT 1")
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Database health check failed: {e}", exc_info=True)
        return {"status": "unhealthy", "error": str(e)}


async def check_redis() -> dict[str, Any]:
    """Check Redis connectivity.

    Returns:
        Dictionary with Redis health status
    """
    try:
        from redis import Redis

        from tempoledger.config import settings

        redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
        redis_client.ping()
        return {"status": "healthy"}
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        return {"status": "degraded", "error": str(e)}


async def check_twilio() -> dict[str, Any]:
    """Check Twilio service status.

    Returns:
        Dictionary with Twilio health status
    """
    try:
        from tempoledger.config import settings

        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            return {"status": "not_configured"}

        # Could check Twilio API health here
        # For now, just check if credentials are configured
        return {"status": "healthy", "configured": True}
    except Exception as e:
        logger.warning(f"Twilio health check failed: {e}")
        return {"status": "degraded", "error": str(e)}


@router.get("/health")
@limiter.limit("100/minute")
async def health_check(request: Request):
    """Health check endpoint."""
    return {"status": "healthy", "service": "tempoledger"}


@router.get("/health/ready")
@limiter.limit("100/minute")
async def readiness_check(request: Request):
    """Readiness check endpoint with component status."""
    db_health = await check_database()
    redis_health = await check_redis()
    twilio_health = await check_twilio()

    # Overall status
    overall_status = "ready"
    if db_health.get("status") != "healthy":
        overall_status = "not_ready"

    return {
        "status": overall_status,
        "components": {
            "database": db_health,
            "redis": redis_health,
            "twilio": twilio_health,
        },
    }


@router.get("/health/live")
@limiter.limit("100/minute")
async def liveness_check(request: Request):
    """Liveness check endpoint."""
    return {"status": "alive"}
