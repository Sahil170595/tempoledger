"""Campaign models."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from tempoledger.models.messages import Message


class CampaignStatus(str, Enum):
    """Campaign status enumeration."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Campaign(BaseModel):
    """Represents a messaging campaign."""

    id: UUID = Field(default_factory=uuid4, description="Unique campaign identifier")
    name: str = Field(..., description="Campaign name", min_length=1)
    total_messages: int = Field(..., description="Total number of messages", gt=0)
    duration_hours: float = Field(..., description="Campaign duration in hours", gt=0)
    messages: list[Message] = Field(..., description="List of messages to send")
    status: CampaignStatus = Field(default=CampaignStatus.DRAFT, description="Campaign status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Creation timestamp"
    )
    started_at: datetime | None = Field(default=None, description="Campaign start time")
    completed_at: datetime | None = Field(default=None, description="Campaign completion time")
    metadata: dict = Field(default_factory=dict, description="Additional campaign metadata")

    model_config = ConfigDict(
        json_encoders_mode="custom",
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def validate_count(self):
        if self.total_messages != len(self.messages):
            raise ValueError("total_messages must equal the number of supplied messages")
        return self

    @field_serializer("created_at", "started_at", "completed_at", when_used="json")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        """Serialize datetime to ISO format."""
        return value.isoformat() if value else None

    @field_serializer("id", when_used="json")
    def serialize_uuid(self, value: UUID) -> str:
        """Serialize UUID to string."""
        return str(value)
