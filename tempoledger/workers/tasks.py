"""Celery tasks for message processing."""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from tempoledger.config import settings
from tempoledger.exceptions import ConfigurationError
from tempoledger.services.database import db
from tempoledger.services.telemetry import telemetry
from tempoledger.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Lazy SMS gateway - created only when needed
# Import is deferred to avoid requiring Twilio at module import time
_sms_gateway = None


def get_sms_gateway():
    """
    Get or create SMS gateway instance (Twilio or Mock).

    Returns:
        SMSGateway or MockSMSGateway instance

    Note:
        Falls back to MockSMSGateway if Twilio is not configured, allowing
        the system to run without Twilio credentials (for development convenience).
    """
    global _sms_gateway
    if _sms_gateway is None:
        # Lazy import to avoid requiring Twilio at module import time
        from tempoledger.services.sms import get_sms_gateway as _get_sms_gateway

        _sms_gateway = _get_sms_gateway()
    return _sms_gateway


@celery_app.task(
    name="send_scheduled_message",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def send_scheduled_message(self, message_id: str, session_id: str, to: str, content: str):
    """
    Send a scheduled message via SMS.

    Args:
        message_id: Message identifier
        session_id: Session/campaign identifier
        to: Recipient phone number
        content: Message content
    """
    import asyncio

    async def _send():
        try:
            # Ensure database is connected
            if db.pool is None:
                await db.connect()

            # Get SMS gateway
            gateway = get_sms_gateway()

            # Send message
            result = await gateway.send_message(
                to=to,
                message=content,
                message_id=UUID(message_id),
                metadata={"session_id": session_id},
            )

            # Update database
            await db.execute(
                """
                UPDATE scheduled_messages
                SET actual_send_time = NOW(),
                    delivery_status = $1,
                    delivery_metadata = $2
                WHERE id = $3
                """,
                result.get("status", "sent"),
                json.dumps(result),
                UUID(message_id),
            )

            # Record telemetry
            telemetry.record_message_sent(
                message_id=message_id,
                session_id=session_id,
                delivery_status=result.get("status"),
            )

            logger.info(
                f"Message sent: {message_id}",
                extra={"message_id": message_id, "session_id": session_id},
            )

        except Exception as e:
            logger.error(f"Failed to send message: {e}", exc_info=True)
            telemetry.record_delivery_failure(
                message_id=message_id, session_id=session_id, reason=str(e)
            )
            # Retry on transient errors
            if isinstance(e, (ConnectionError, TimeoutError)):
                raise self.retry(exc=e, countdown=60)
            raise

    # Celery tasks run in separate processes, so we can use asyncio.run()
    # But if called from tests with an existing loop, we need to handle it
    try:
        # Check if we're in a running event loop (e.g., from tests)
        asyncio.get_running_loop()
        # If we get here, we're in a running loop - use ThreadPoolExecutor
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_send()))
            return future.result()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(_send())


@celery_app.task(
    name="process_message_queue",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    retry_backoff_max=300,
)
def process_message_queue(self):
    """Process scheduled messages that are due."""
    import asyncio

    async def _process():
        try:
            if settings.enable_live_delivery:
                raise ConfigurationError(
                    "Queue dispatcher has no recipient mapping; simulation only"
                )
            # Create a fresh database connection for this event loop
            # The global db.pool might be tied to a different event loop
            import asyncpg

            conn = await asyncpg.connect(str(db.database_url))
            try:
                # Get messages due to be sent
                now = datetime.now(UTC)
                rows = await conn.fetch(
                    """
                    SELECT id, session_id, message_content, scheduled_time
                    FROM scheduled_messages
                    WHERE scheduled_time <= $1
                      AND actual_send_time IS NULL
                      AND delivery_status IS NULL
                    ORDER BY scheduled_time
                    LIMIT 10
                    """,
                    now,
                )

                messages = [dict(row) for row in rows]

                for msg in messages:
                    # Schedule sending task
                    # Note: In production, you'd need recipient phone number
                    send_scheduled_message.delay(
                        message_id=str(msg["id"]),
                        session_id=str(msg["session_id"]),
                        to="synthetic:queue",
                        content=msg["message_content"],
                    )

                logger.info(f"Processed {len(messages)} messages from queue")
            finally:
                await conn.close()

        except Exception as e:
            logger.error(f"Failed to process message queue: {e}", exc_info=True)
            # Retry on transient errors
            if isinstance(e, (ConnectionError, TimeoutError)):
                raise self.retry(exc=e, countdown=30)
            raise

    # Celery tasks run in separate processes, so we can use asyncio.run()
    # But if called from tests with an existing loop, we need to handle it
    try:
        # Check if we're in a running event loop (e.g., from tests)
        asyncio.get_running_loop()
        # If we get here, we're in a running loop - use ThreadPoolExecutor
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_process()))
            return future.result()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(_process())
