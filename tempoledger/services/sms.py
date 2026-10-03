"""SMS gateway adapter for Twilio integration."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

try:
    from twilio.base.exceptions import TwilioException
    from twilio.rest import Client as TwilioClient

    TWILIO_AVAILABLE = True
except ImportError:
    # Twilio not installed - module can still be imported
    TWILIO_AVAILABLE = False
    TwilioClient = None  # type: ignore
    TwilioException = Exception  # type: ignore

from tempoledger.config import settings
from tempoledger.exceptions import ConfigurationError, SMSError
from tempoledger.services.retry import retry_sms

logger = logging.getLogger(__name__)


class MockSMSGateway:
    """Mock SMS gateway for development/testing when Twilio is not configured."""

    def __init__(self, config=None):
        """
        Initialize mock SMS gateway.

        Args:
            config: Optional configuration (uses settings if not provided)
        """
        self.config = config or settings
        self._sent_messages: dict[str, dict] = {}  # Store mock messages by SID

    async def send_message(
        self,
        to: str,
        message: str,
        message_id: UUID | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """
        Mock sending an SMS message.

        Args:
            to: Recipient phone number (E.164 format)
            message: Message content
            message_id: Optional message identifier for tracking
            metadata: Optional metadata

        Returns:
            Dictionary with delivery status and metadata (simulated)
        """
        # Generate a mock SID
        mock_sid = f"SM{str(uuid4()).replace('-', '').upper()[:32]}"
        now = datetime.now(UTC)

        # Simulate delivery (usually successful in mock mode)
        status = "queued"  # Will progress to "sent" after a short delay

        result = {
            "status": status,
            "sid": mock_sid,
            "date_created": now.isoformat(),
            "price": None,
            "simulated": True,
            "error_code": None,
            "error_message": None,
        }

        # Store for later status checks
        self._sent_messages[mock_sid] = {
            **result,
            "to": to,
            "message": message,
            "message_id": str(message_id) if message_id else None,
            "metadata": metadata,
        }

        logger.info(
            f"[MOCK] Message simulated: {mock_sid}",
            extra={
                "message_id": str(message_id) if message_id else None,
                "twilio_sid": mock_sid,
                "to": to,
                "mock": True,
            },
        )

        # Simulate async delivery progression
        asyncio.create_task(self._simulate_delivery(mock_sid))

        return result

    async def _simulate_delivery(self, sid: str, delay: float = 0.5):
        """Simulate message delivery progression."""
        await asyncio.sleep(delay)
        if sid in self._sent_messages:
            self._sent_messages[sid]["status"] = "sent"
            self._sent_messages[sid]["date_sent"] = datetime.now(UTC).isoformat()

    async def get_message_status(self, sid: str) -> dict:
        """
        Get the status of a mock message by SID.

        Args:
            sid: Mock message SID

        Returns:
            Dictionary with message status and metadata
        """
        if sid not in self._sent_messages:
            raise SMSError(f"Message not found: {sid}")

        message = self._sent_messages[sid]
        return {
            "status": message.get("status", "sent"),
            "sid": message["sid"],
            "date_created": message.get("date_created"),
            "date_sent": message.get("date_sent"),
            "date_updated": datetime.now(UTC).isoformat(),
            "price": message.get("price"),
            "error_code": message.get("error_code"),
            "error_message": message.get("error_message"),
            "simulated": True,
        }


class SMSGateway:
    """SMS gateway adapter for Twilio integration."""

    def __init__(self, config=None):
        """
        Initialize SMS gateway.

        Args:
            config: Optional configuration (uses settings if not provided)

        Raises:
            ConfigurationError: If Twilio is not available or credentials are missing
            SMSError: If Twilio client initialization fails
        """
        self.config = config or settings

        if not self.config.enable_live_delivery:
            raise ConfigurationError("Live delivery is disabled; use MockSMSGateway")
        if not TWILIO_AVAILABLE:
            raise ConfigurationError(
                "Twilio package not installed. Install with: pip install twilio"
            )

        if not self.config.twilio_account_sid or not self.config.twilio_auth_token:
            raise ConfigurationError(
                "Twilio credentials not configured. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
            )

        if not self.config.twilio_phone_number:
            raise ConfigurationError("Twilio phone number not configured. Set TWILIO_PHONE_NUMBER.")

        try:
            self.client = TwilioClient(
                self.config.twilio_account_sid, self.config.twilio_auth_token
            )
        except Exception as e:
            raise SMSError(f"Failed to initialize Twilio client: {e}") from e

    @retry_sms
    async def send_message(
        self,
        to: str,
        message: str,
        message_id: UUID | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """
        Send an SMS message via Twilio.

        Args:
            to: Recipient phone number (E.164 format)
            message: Message content
            message_id: Optional message identifier for tracking
            metadata: Optional metadata to include in webhook

        Returns:
            Dictionary with delivery status and metadata

        Raises:
            SMSError: If sending fails
        """
        try:
            # Prepare status callback URL if configured
            status_callback = None
            if metadata and "webhook_url" in metadata:
                status_callback = metadata["webhook_url"]

            # Send message - offload synchronous Twilio call to thread pool
            def _send_message():
                return self.client.messages.create(
                    body=message,
                    from_=self.config.twilio_phone_number,
                    to=to,
                    status_callback=status_callback,
                )

            twilio_message = await asyncio.to_thread(_send_message)

            logger.info(
                f"Message sent via Twilio: {twilio_message.sid}",
                extra={
                    "message_id": str(message_id) if message_id else None,
                    "twilio_sid": twilio_message.sid,
                    "to": to,
                },
            )

            return {
                "status": twilio_message.status,
                "sid": twilio_message.sid,
                "date_created": (
                    twilio_message.date_created.isoformat() if twilio_message.date_created else None
                ),
                "price": str(twilio_message.price) if twilio_message.price else None,
                "error_code": twilio_message.error_code,
                "error_message": twilio_message.error_message,
            }

        except TwilioException as e:
            logger.error(
                f"Twilio error sending message: {e}",
                extra={"message_id": str(message_id) if message_id else None, "to": to},
                exc_info=True,
            )
            raise SMSError(f"Twilio error: {e}") from e

        except Exception as e:
            logger.error(
                f"Unexpected error sending message: {e}",
                extra={"message_id": str(message_id) if message_id else None, "to": to},
                exc_info=True,
            )
            raise SMSError(f"Failed to send message: {e}") from e

    @retry_sms
    async def get_message_status(self, sid: str) -> dict:
        """
        Get the status of a message by Twilio SID.

        Args:
            sid: Twilio message SID

        Returns:
            Dictionary with message status and metadata

        Raises:
            SMSError: If lookup fails
        """
        try:
            # Offload synchronous Twilio call to thread pool
            def _fetch_message():
                return self.client.messages(sid).fetch()

            message = await asyncio.to_thread(_fetch_message)

            return {
                "status": message.status,
                "sid": message.sid,
                "date_created": message.date_created.isoformat() if message.date_created else None,
                "date_sent": message.date_sent.isoformat() if message.date_sent else None,
                "date_updated": message.date_updated.isoformat() if message.date_updated else None,
                "price": str(message.price) if message.price else None,
                "error_code": message.error_code,
                "error_message": message.error_message,
            }

        except TwilioException as e:
            logger.error(f"Twilio error fetching message status: {e}", exc_info=True)
            raise SMSError(f"Twilio error: {e}") from e

        except Exception as e:
            logger.error(f"Unexpected error fetching message status: {e}", exc_info=True)
            raise SMSError(f"Failed to fetch message status: {e}") from e


def get_sms_gateway():
    """
    Get or create SMS gateway instance (Twilio or Mock).

    Returns:
        SMSGateway or MockSMSGateway instance

    Note:
        Falls back to MockSMSGateway if Twilio is not configured, allowing
        the system to run without Twilio credentials (for development convenience).
    """
    # Try Twilio first
    if TWILIO_AVAILABLE and settings.enable_live_delivery:
        if (
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_phone_number
        ):
            return SMSGateway()

    # Fall back to mock mode
    logger.warning(
        "Twilio not configured - using MockSMSGateway. Messages will be simulated, not actually sent."
    )
    return MockSMSGateway()
