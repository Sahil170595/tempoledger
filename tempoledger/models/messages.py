"""Message models."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class Message(BaseModel):
    """Represents a message to be sent."""

    id: UUID = Field(default_factory=uuid4, description="Unique message identifier")
    content: str = Field(..., description="Message content", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Message content must contain non-whitespace text")
        return value


class ScheduledMessage(BaseModel):
    """Represents a message with scheduling information."""

    message: Message = Field(..., description="The message to send")
    send_time: datetime = Field(..., description="Scheduled send time")
    reasoning: dict[str, Any] = Field(
        default_factory=dict, description="Scheduling decision reasoning"
    )
    session_id: UUID = Field(..., description="Session/campaign identifier")
    actual_send_time: datetime | None = Field(
        default=None, description="Actual time message was sent"
    )
    delivery_status: str | None = Field(
        default=None, description="Delivery status (pending, sent, delivered, failed)"
    )
    delivery_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Delivery metadata from carrier"
    )

    model_config = ConfigDict(
        json_encoders_mode="custom",
    )

    @field_serializer("send_time", "actual_send_time", when_used="json")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        """Serialize datetime to ISO format."""
        return value.isoformat() if value else None

    @field_serializer("message", when_used="json")
    def serialize_message(self, value: Message) -> dict[str, Any]:
        """Serialize message."""
        return value.model_dump(mode="json")

    @field_serializer("session_id", when_used="json")
    def serialize_uuid(self, value: UUID) -> str:
        """Serialize UUID to string."""
        return str(value)
