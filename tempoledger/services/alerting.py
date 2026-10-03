"""Alerting service for pattern health and system monitoring."""

import logging
from enum import Enum
from typing import Any

from tempoledger.models.sessions import PatternHealthMetrics

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannel:
    """Base class for alert channels."""

    def send(self, severity: AlertSeverity, message: str, metadata: dict[str, Any] | None = None):
        """Send an alert.

        Args:
            severity: Alert severity level
            message: Alert message
            metadata: Optional metadata
        """
        raise NotImplementedError


class LoggingAlertChannel(AlertChannel):
    """Alert channel that logs alerts."""

    def send(self, severity: AlertSeverity, message: str, metadata: dict[str, Any] | None = None):
        """Send alert via logging."""
        log_level = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.CRITICAL: logging.CRITICAL,
        }.get(severity, logging.WARNING)

        logger.log(
            log_level,
            f"ALERT [{severity.value.upper()}]: {message}",
            extra={"severity": severity.value, "metadata": metadata or {}},
        )


class AlertingService:
    """Service for handling alerts based on pattern health metrics."""

    def __init__(self, channels: list[AlertChannel] | None = None):
        """Initialize alerting service.

        Args:
            channels: List of alert channels (defaults to logging only)
        """
        self.channels = channels or [LoggingAlertChannel()]

    def check_pattern_health(self, metrics: PatternHealthMetrics, session_id: str):
        """Check pattern health metrics and send alerts if needed.

        Args:
            metrics: Pattern health metrics
            session_id: Session/campaign identifier
        """
        # Check delivery rate
        if metrics.delivery_rate < 0.85:
            self.send_alert(
                AlertSeverity.WARNING,
                f"Low delivery rate: {metrics.delivery_rate:.2%} (threshold: 85%)",
                {
                    "session_id": session_id,
                    "metric": "delivery_rate",
                    "value": metrics.delivery_rate,
                    "threshold": 0.85,
                },
            )

        # Check operator flags
        if metrics.operator_flags > 0:
            self.send_alert(
                AlertSeverity.CRITICAL,
                f"OPERATOR FLAG RECEIVED: {metrics.operator_flags} flag(s) for session {session_id}",
                {
                    "session_id": session_id,
                    "metric": "operator_flags",
                    "value": metrics.operator_flags,
                },
            )

        # Check interval variance
        if metrics.interval_variance < 0.3:
            self.send_alert(
                AlertSeverity.WARNING,
                f"Low interval variance: {metrics.interval_variance:.2f} (threshold: 0.3)",
                {
                    "session_id": session_id,
                    "metric": "interval_variance",
                    "value": metrics.interval_variance,
                    "threshold": 0.3,
                },
            )

        # Check health score
        if metrics.health_score < 0.8:
            self.send_alert(
                AlertSeverity.WARNING,
                f"Low pattern health score: {metrics.health_score:.2f} (threshold: 0.8)",
                {
                    "session_id": session_id,
                    "metric": "health_score",
                    "value": metrics.health_score,
                    "threshold": 0.8,
                },
            )

    def send_alert(
        self,
        severity: AlertSeverity,
        message: str,
        metadata: dict[str, Any] | None = None,
    ):
        """Send an alert through all channels.

        Args:
            severity: Alert severity level
            message: Alert message
            metadata: Optional metadata
        """
        for channel in self.channels:
            try:
                channel.send(severity, message, metadata)
            except Exception as e:
                logger.error(
                    f"Failed to send alert via channel {channel.__class__.__name__}: {e}",
                    exc_info=True,
                )

    def add_channel(self, channel: AlertChannel):
        """Add an alert channel.

        Args:
            channel: Alert channel to add
        """
        self.channels.append(channel)


# Global alerting service instance
alerting = AlertingService()
