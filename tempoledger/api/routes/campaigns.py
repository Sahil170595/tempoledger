"""Campaign management endpoints."""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from tempoledger.api.middleware.rate_limit import limiter
from tempoledger.models import Campaign, CampaignStatus
from tempoledger.scheduling.engine import JitterScheduler
from tempoledger.services.database import db
from tempoledger.services.telemetry import telemetry

logger = logging.getLogger(__name__)

router = APIRouter()
scheduler = JitterScheduler()


class CampaignCreateRequest(BaseModel):
    """Request model for creating a campaign."""

    name: str = Field(..., min_length=1, description="Campaign name")
    total_messages: int = Field(..., gt=0, description="Total number of messages")
    duration_hours: float = Field(
        ..., gt=0, allow_inf_nan=False, description="Campaign duration in hours"
    )
    messages: list[dict] = Field(..., min_length=1, description="List of message content dicts")


class CampaignResponse(BaseModel):
    """Response model for campaign."""

    id: str
    name: str
    status: str
    total_messages: int
    duration_hours: float
    messages_scheduled: int
    created_at: str


@router.post("/campaigns", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_campaign(request: Request, campaign_request: CampaignCreateRequest):
    """
    Create a new campaign and schedule messages.

    Args:
        request: Campaign creation request

    Returns:
        Campaign response with scheduling results
    """
    try:
        # Validate input
        if not campaign_request.messages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one message is required",
            )

        # Create campaign
        from tempoledger.models import Message

        messages = [
            Message(content=msg.get("content", ""), metadata=msg.get("metadata", {}))
            for msg in campaign_request.messages
        ]

        # Validate that all messages have content
        for msg in messages:
            if not msg.content or not msg.content.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All messages must have non-empty content",
                )

        campaign = Campaign(
            name=campaign_request.name,
            total_messages=campaign_request.total_messages,
            duration_hours=campaign_request.duration_hours,
            messages=messages,
            status=CampaignStatus.SCHEDULED,
        )

        # Schedule messages
        schedule = scheduler.schedule_campaign(campaign)

        # Save to database
        for scheduled in schedule:
            await db.execute(
                """
                INSERT INTO scheduled_messages
                (id, session_id, message_content, scheduled_time, scheduling_metadata, created_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
                """,
                scheduled.message.id,
                campaign.id,
                scheduled.message.content,
                scheduled.send_time,
                json.dumps(scheduled.reasoning) if scheduled.reasoning else None,
            )

        # Record telemetry
        telemetry.record_scheduling_decision(
            message_id=str(campaign.messages[0].id) if campaign.messages else "unknown",
            scheduled_time=schedule[0].send_time if schedule else None,
            reasoning={"campaign_id": str(campaign.id), "message_count": len(schedule)},
            session_id=str(campaign.id),
        )

        logger.info(
            f"Campaign created: {campaign.id}",
            extra={"campaign_id": str(campaign.id), "message_count": len(schedule)},
        )

        return CampaignResponse(
            id=str(campaign.id),
            name=campaign.name,
            status=campaign.status.value,
            total_messages=campaign.total_messages,
            duration_hours=campaign.duration_hours,
            messages_scheduled=len(schedule),
            created_at=campaign.created_at.isoformat(),
        )

    except HTTPException:
        raise
    except PydanticValidationError as e:
        logger.warning(f"Validation error creating campaign: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}",
        ) from e
    except Exception as e:
        logger.error(f"Failed to create campaign: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create campaign: {str(e)}",
        ) from e


@router.get("/campaigns/{campaign_id}", response_model=dict)
async def get_campaign(campaign_id: UUID):
    """
    Get campaign details.

    Args:
        campaign_id: Campaign identifier

    Returns:
        Campaign details with scheduled messages
    """
    try:
        # Get scheduled messages
        messages = await db.fetch(
            """
            SELECT id, message_content, scheduled_time, actual_send_time,
                   delivery_status, scheduling_metadata, delivery_metadata
            FROM scheduled_messages
            WHERE session_id = $1
            ORDER BY scheduled_time
            """,
            campaign_id,
        )

        if not messages:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")

        return {
            "id": str(campaign_id),
            "messages": messages,
            "total_messages": len(messages),
            "scheduled": len([m for m in messages if m.get("scheduled_time")]),
            "sent": len([m for m in messages if m.get("actual_send_time")]),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get campaign: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get campaign: {str(e)}",
        ) from e


@router.get("/campaigns", response_model=list[dict])
async def list_campaigns(limit: int = 50, offset: int = 0):
    """
    List campaigns.

    Args:
        limit: Maximum number of campaigns to return
        offset: Offset for pagination

    Returns:
        List of campaign summaries
    """
    # Validate parameters
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Limit must be between 1 and 1000",
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Offset must be non-negative",
        )

    try:
        campaigns = await db.fetch(
            """
            SELECT DISTINCT session_id,
                   COUNT(*) as message_count,
                   MIN(scheduled_time) as created_at
            FROM scheduled_messages
            GROUP BY session_id
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

        return [
            {
                "id": str(c["session_id"]),
                "message_count": c["message_count"],
                "created_at": c["created_at"].isoformat() if c["created_at"] else None,
            }
            for c in campaigns
        ]

    except Exception as e:
        logger.error(f"Failed to list campaigns: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list campaigns: {str(e)}",
        ) from e
