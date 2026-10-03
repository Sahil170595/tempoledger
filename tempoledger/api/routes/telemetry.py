"""Telemetry endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from tempoledger.api.middleware.rate_limit import limiter
from tempoledger.services.database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/telemetry/health/{session_id}", response_model=dict)
@limiter.limit("50/minute")
async def get_pattern_health(request: Request, session_id: UUID):
    """
    Get pattern health metrics for a session.

    Args:
        session_id: Session/campaign identifier

    Returns:
        Pattern health metrics
    """
    try:
        # Get scheduled messages
        messages = await db.fetch(
            """
            SELECT scheduled_time, actual_send_time, delivery_status
            FROM scheduled_messages
            WHERE session_id = $1
            ORDER BY scheduled_time
            """,
            session_id,
        )

        if not messages:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        # Calculate metrics
        intervals = []
        for i in range(1, len(messages)):
            if messages[i].get("scheduled_time") and messages[i - 1].get("scheduled_time"):
                interval = (
                    messages[i]["scheduled_time"] - messages[i - 1]["scheduled_time"]
                ).total_seconds()
                intervals.append(interval)

        # Calculate statistics
        import numpy as np

        if intervals:
            variance = float(np.var(intervals))
            mean_interval = float(np.mean(intervals))
            coefficient_of_variation = (
                float(np.std(intervals)) / mean_interval if mean_interval > 0 else 0.0
            )
        else:
            variance = 0.0
            coefficient_of_variation = 0.0

        # Delivery metrics
        total_messages = len(messages)
        delivered = len([m for m in messages if m.get("delivery_status") == "delivered"])
        failed = len([m for m in messages if m.get("delivery_status") == "failed"])
        delivery_rate = delivered / total_messages if total_messages > 0 else 0.0
        bounce_rate = failed / total_messages if total_messages > 0 else 0.0

        # Health score (simplified)
        health_score = min(1.0, max(0.0, delivery_rate * 0.7 + (1.0 - bounce_rate) * 0.3))

        return {
            "session_id": str(session_id),
            "metrics": {
                "interval_variance": variance,
                "coefficient_of_variation": coefficient_of_variation,
                "delivery_rate": delivery_rate,
                "bounce_rate": bounce_rate,
                "health_score": health_score,
                "total_messages": total_messages,
                "delivered": delivered,
                "failed": failed,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pattern health: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get pattern health: {str(e)}",
        ) from e
