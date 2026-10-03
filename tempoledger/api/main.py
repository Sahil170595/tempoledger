"""FastAPI application entry point."""

import logging
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError as PydanticValidationError
from slowapi.errors import RateLimitExceeded

from tempoledger.api.middleware.error_handler import error_handler
from tempoledger.api.middleware.rate_limit import limiter, rate_limit_handler
from tempoledger.api.routes import campaigns, health, telemetry
from tempoledger.config import settings
from tempoledger.exceptions import (
    AgentError,
    CircuitBreakerError,
    DatabaseError,
    RateLimitError,
    SchedulingError,
    SMSError,
    TempoledgerError,
)
from tempoledger.logging_config import configure_logging
from tempoledger.services.database import db

# Configure logging
configure_logging(level="INFO" if not settings.debug else "DEBUG")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Tempoledger API...")
    try:
        await db.connect()
        # Run database migrations
        try:
            logger.info("Running database migrations...")
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.warning(f"Migration warning: {result.stderr}")
            else:
                logger.info("Database migrations completed")
        except Exception as e:
            logger.warning(f"Could not run migrations: {e}. Using create_schema fallback.")
            await db.create_schema()
        logger.info("Database connected and schema verified")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        raise

    yield

    # Shutdown
    logger.info("Shutting down Tempoledger API...")
    await db.disconnect()
    logger.info("Database disconnected")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="stochastic SMS Scheduling System",
    lifespan=lifespan,
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

# Add error handlers
app.add_exception_handler(PydanticValidationError, error_handler)
app.add_exception_handler(RateLimitError, error_handler)
app.add_exception_handler(CircuitBreakerError, error_handler)
app.add_exception_handler(DatabaseError, error_handler)
app.add_exception_handler(SMSError, error_handler)
app.add_exception_handler(AgentError, error_handler)
app.add_exception_handler(SchedulingError, error_handler)
app.add_exception_handler(TempoledgerError, error_handler)
app.add_exception_handler(Exception, error_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(campaigns.router, prefix="/api/v1", tags=["campaigns"])
app.include_router(telemetry.router, prefix="/api/v1", tags=["telemetry"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }
