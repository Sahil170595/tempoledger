"""Rate limiting middleware for API endpoints."""

import logging

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from tempoledger.config import settings

logger = logging.getLogger(__name__)

# Create rate limiter with Redis backend if available
try:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=settings.rate_limit_storage_uri,
        default_limits=["1000/hour"],
    )
except Exception as e:
    logger.warning(f"Could not initialize Redis for rate limiting: {e}. Using in-memory storage.")
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["1000/hour"],
    )

# Rate limit configurations per endpoint
RATE_LIMITS = {
    "/api/v1/campaigns": "10/minute",
    "/api/v1/health": "100/minute",
    "/api/v1/telemetry": "50/minute",
}


def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    logger.warning(
        f"Rate limit exceeded for {request.url.path}",
        extra={
            "path": request.url.path,
            "client": get_remote_address(request),
        },
    )
    # Calculate retry_after from the limit (default to 60 seconds)
    retry_after = 60
    if hasattr(exc, "retry_after"):
        retry_after = exc.retry_after
    elif hasattr(exc, "detail") and "minute" in str(exc.detail):
        retry_after = 60
    elif hasattr(exc, "detail") and "hour" in str(exc.detail):
        retry_after = 3600

    response = Response(
        content=f"Rate limit exceeded: {exc.detail if hasattr(exc, 'detail') else str(exc)}",
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
    return response


# Export limiter for use in route decorators
__all__ = ["limiter", "RATE_LIMITS", "rate_limit_handler"]
