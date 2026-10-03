"""Event models for event-driven architecture."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class EventType(str, Enum):
    """Scheduling event types."""

    MESSAGE_QUEUED = "message_queued"
    TYPING_STARTED = "typing_started"
    MESSAGE_SENT = "message_sent"
    DELIVERY_CONFIRMED = "delivery_confirmed"
    REPLY_RECEIVED = "reply_received"
    OPERATOR_FLAGGED = "operator_flagged"
    PATTERN_ANOMALY = "pattern_anomaly"
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_PAUSED = "campaign_paused"
    CAMPAIGN_COMPLETED = "campaign_completed"


class SchedulingEvent(BaseModel):
    """Represents a scheduling lifecycle event."""

    id: UUID = Field(default_factory=uuid4, description="Unique event identifier")
    event_type: EventType = Field(..., description="Type of event")
    session_id: UUID = Field(..., description="Session/campaign identifier")
    message_id: UUID | None = Field(default=None, description="Related message identifier")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Event timestamp"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Event-specific metadata")

    model_config = ConfigDict(
        json_encoders_mode="custom",
    )

    @field_serializer("timestamp", when_used="json")
    def serialize_datetime(self, value: datetime) -> str:
        """Serialize datetime to ISO format."""
        return value.isoformat()

    @field_serializer("id", "session_id", "message_id", when_used="json")
    def serialize_uuid(self, value: UUID | None) -> str | None:
        """Serialize UUID to string."""
        return str(value) if value else None
