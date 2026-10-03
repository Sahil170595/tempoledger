"""Error handling middleware for API."""

import logging
import uuid
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from tempoledger.exceptions import (
    AgentError,
    CircuitBreakerError,
    DatabaseError,
    RateLimitError,
    SchedulingError,
    SMSError,
    TempoledgerError,
)

logger = logging.getLogger(__name__)


class ErrorResponse:
    """Structured error response."""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        """Initialize error response.

        Args:
            error_code: Error code identifier
            message: Human-readable error message
            status_code: HTTP status code
            request_id: Optional request identifier for correlation
            details: Optional additional error details
        """
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert error response to dictionary."""
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "request_id": self.request_id,
                "details": self.details,
            }
        }


async def error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle exceptions and return structured error responses.

    Args:
        request: FastAPI request
        exc: Exception to handle

    Returns:
        JSONResponse with error details
    """
    # Generate request ID for correlation
    request_id = str(uuid.uuid4())

    # Log error with context
    logger.error(
        f"Error handling request: {exc}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "error_type": type(exc).__name__,
        },
        exc_info=exc,
    )

    # Handle specific exception types
    from pydantic import ValidationError as PydanticValidationError

    if isinstance(exc, PydanticValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error_code="VALIDATION_ERROR",
                message="Validation failed",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                request_id=request_id,
                details={"errors": exc.errors()},
            ).to_dict(),
        )

    if isinstance(exc, ValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="VALIDATION_ERROR",
                message=str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, RateLimitError):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=ErrorResponse(
                error_code="RATE_LIMIT_EXCEEDED",
                message=str(exc),
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, CircuitBreakerError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SERVICE_UNAVAILABLE",
                message="Service temporarily unavailable",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                request_id=request_id,
                details={"reason": "circuit_breaker_open"},
            ).to_dict(),
        )

    if isinstance(exc, DatabaseError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="DATABASE_ERROR",
                message="Database operation failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, SMSError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(
                error_code="SMS_ERROR",
                message="SMS service error",
                status_code=status.HTTP_502_BAD_GATEWAY,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, AgentError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="AGENT_ERROR",
                message="Agent operation failed",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, SchedulingError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="SCHEDULING_ERROR",
                message="Scheduling operation failed",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                request_id=request_id,
            ).to_dict(),
        )

    if isinstance(exc, TempoledgerError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="INTERNAL_ERROR",
                message=str(exc),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                request_id=request_id,
            ).to_dict(),
        )

    # Generic error handler
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
        ).to_dict(),
    )
