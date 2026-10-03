"""Celery application configuration."""

import asyncio
import logging
import os

from celery import Celery
from celery.signals import worker_ready, worker_shutting_down

from tempoledger.config import settings
from tempoledger.services.database import db

logger = logging.getLogger(__name__)

# Create Celery app
celery_app = Celery(
    "tempoledger",
    broker=os.getenv("REDIS_URL", str(settings.redis_url)),
    backend=os.getenv("REDIS_URL", str(settings.redis_url)),
    include=["tempoledger.workers.tasks"],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)


@worker_ready.connect
def worker_ready_handler(sender=None, **kwargs):
    """Initialize database connection when worker starts."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(db.connect())
        logger.info("Database connection established in Celery worker")
    except Exception as e:
        logger.error(f"Failed to connect database in worker: {e}", exc_info=True)
        raise


@worker_shutting_down.connect
def worker_shutting_down_handler(sender=None, **kwargs):
    """Close database connection when worker shuts down."""
    try:
        if db.pool is not None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(db.disconnect())
            logger.info("Database connection closed in Celery worker")
    except Exception as e:
        logger.warning(f"Error closing database connection: {e}", exc_info=True)
