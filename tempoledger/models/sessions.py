"""Session and context models."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class PatternHealthMetrics(BaseModel):
    """Pattern health metrics for operational anomalies."""

    interval_variance: float = Field(..., description="Variance of message intervals (seconds^2)")
    interval_entropy: float = Field(..., description="Shannon entropy of intervals")
    cluster_density: float = Field(..., description="Messages per peak window")
    delivery_rate: float = Field(..., description="Successfully delivered / sent")
    read_rate: float | None = Field(default=None, description="Read receipts / delivered")
    reply_rate: float = Field(..., description="Replies / sent")
    operator_flags: int = Field(default=0, description="Operator flag reports")
    bounce_rate: float = Field(..., description="Failed deliveries / sent")
    block_rate: float = Field(..., description="Number blocked / sent")
    circadian_adherence: float = Field(..., description="Match to configured activity curve (0-1)")
    typing_speed_variance: float = Field(..., description="WPM distribution health")
    coefficient_of_variation: float = Field(..., description="CV of intervals")
    health_score: float = Field(..., description="Overall health score (0-1)")


class SessionContext(BaseModel):
    """Session context for agent decision-making."""

    session_id: UUID = Field(..., description="Session identifier")
    campaign_name: str = Field(..., description="Campaign name")
    messages_sent: int = Field(default=0, description="Number of messages sent")
    total_messages: int = Field(..., description="Total messages in campaign")
    pattern_health: PatternHealthMetrics | None = Field(
        default=None, description="Current pattern health"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Session start time"
    )
    last_event: dict[str, Any] | None = Field(default=None, description="Last event metadata")
    last_action: dict[str, Any] | None = Field(default=None, description="Last agent action")
    parameters: dict[str, float] = Field(
        default_factory=dict, description="Current scheduling parameters"
    )

    model_config = ConfigDict(
        json_encoders_mode="custom",
    )

    @field_serializer("created_at", when_used="json")
    def serialize_datetime(self, value: datetime) -> str:
        """Serialize datetime to ISO format."""
        return value.isoformat()

    @field_serializer("session_id", when_used="json")
    def serialize_uuid(self, value: UUID) -> str:
        """Serialize UUID to string."""
        return str(value)
