"""OpenTelemetry-based telemetry service."""

import logging
from typing import Any

try:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:
    # OpenTelemetry not installed - module can still be imported
    OTEL_AVAILABLE = False
    metrics = None  # type: ignore
    trace = None  # type: ignore
    OTLPMetricExporter = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore
    MeterProvider = None  # type: ignore
    PeriodicExportingMetricReader = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore

from tempoledger.config import settings
from tempoledger.exceptions import ConfigurationError
from tempoledger.models.sessions import PatternHealthMetrics
from tempoledger.services.alerting import AlertSeverity, alerting

logger = logging.getLogger(__name__)


class TelemetryService:
    """
    OpenTelemetry-based instrumentation service.

    Tracks:
    - Scheduling decisions (spans)
    - Pattern health metrics (gauges)
    - Agent actions (events)
    - Delivery outcomes (counters)
    """

    def __init__(self):
        """
        Initialize telemetry service.

        Raises:
            ConfigurationError: If OpenTelemetry is not available
        """
        if not OTEL_AVAILABLE:
            raise ConfigurationError(
                "OpenTelemetry package not installed. Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc"
            )

        self._setup_tracing()
        self._setup_metrics()

    def _setup_tracing(self):
        """Set up distributed tracing."""
        if settings.otel_exporter_otlp_endpoint:
            try:
                trace_exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
                span_processor = BatchSpanProcessor(trace_exporter)
                tracer_provider = TracerProvider()
                tracer_provider.add_span_processor(span_processor)
                trace.set_tracer_provider(tracer_provider)
            except Exception as e:
                logger.warning(f"Failed to set up OTLP tracing: {e}")

        self.tracer = trace.get_tracer("tempoledger.scheduling", "1.0.0")

    def _setup_metrics(self):
        """Set up metrics collection."""
        if settings.otel_exporter_otlp_endpoint:
            try:
                metric_exporter = OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint)
                metric_reader = PeriodicExportingMetricReader(
                    metric_exporter, export_interval_millis=5000
                )
                meter_provider = MeterProvider(metric_readers=[metric_reader])
                metrics.set_meter_provider(meter_provider)
            except Exception as e:
                logger.warning(f"Failed to set up OTLP metrics: {e}")

        self.meter = metrics.get_meter("tempoledger.scheduling", "1.0.0")

        # Create metrics
        self.messages_sent = self.meter.create_counter(
            "messages_sent_total",
            description="Total messages sent",
            unit="1",
        )
        # Use a histogram for pattern health (better than counter for gauge values)
        # Observable gauge requires callbacks which is complex, so we use histogram
        self.pattern_health_histogram = self.meter.create_histogram(
            "pattern_health_score",
            description="0-1 score of pattern quality (instantaneous)",
            unit="1",
        )
        # Store latest values for potential observable gauge conversion
        self._pattern_health_values: dict[str, tuple[float, dict]] = {}
        self.operator_flags = self.meter.create_counter(
            "operator_flags_total",
            description="Operator-flagged events",
            unit="1",
        )
        self.delivery_failures = self.meter.create_counter(
            "delivery_failures_total",
            description="Failed message deliveries",
            unit="1",
        )

    def record_scheduling_decision(
        self,
        message_id: str,
        scheduled_time: Any,
        reasoning: dict[str, Any],
        session_id: str | None = None,
    ):
        """
        Log a scheduling decision with full context.

        Args:
            message_id: Unique message identifier
            scheduled_time: Scheduled send time
            reasoning: Scheduling decision reasoning
            session_id: Optional session identifier
        """
        with self.tracer.start_as_current_span("schedule_message") as span:
            span.set_attribute("message_id", message_id)
            if session_id:
                span.set_attribute("session_id", session_id)
            if "jitter_applied" in reasoning:
                span.set_attribute("jitter_applied_seconds", reasoning["jitter_applied"])
            if "had_pause" in reasoning:
                span.set_attribute("had_pause", reasoning["had_pause"])
            if "wpm_sampled" in reasoning:
                span.set_attribute("wpm_sampled", reasoning["wpm_sampled"])

            span.add_event(
                "scheduling_decision",
                attributes={k: str(v) for k, v in reasoning.items() if v is not None},
            )

    def record_message_sent(
        self,
        message_id: str,
        session_id: str | None = None,
        delivery_status: str | None = None,
    ):
        """
        Record a message sent event.

        Args:
            message_id: Unique message identifier
            session_id: Optional session identifier
            delivery_status: Optional delivery status
        """
        # Build attributes dict with all values
        attributes = {"message_id": message_id}
        if session_id:
            attributes["session_id"] = session_id
        if delivery_status:
            attributes["delivery_status"] = delivery_status

        # Emit single counter increment with all attributes
        self.messages_sent.add(1, attributes=attributes)

    def record_pattern_health(
        self,
        score: float,
        session_id: str | None = None,
        metrics: PatternHealthMetrics | None = None,
    ):
        """
        Record pattern health score.

        Note: Uses a histogram to record instantaneous values. Each call
        records the current score, not an accumulated value.

        Args:
            score: Health score (0-1)
            session_id: Optional session identifier
            metrics: Optional full pattern health metrics for alerting
        """
        attributes = {}
        if session_id:
            attributes["session_id"] = session_id

        # Record as histogram value (represents current state, not accumulated)
        # Histogram records the value, allowing us to track current state
        self.pattern_health_histogram.record(score, attributes=attributes)

        # Store latest value for potential future use
        key = session_id or "default"
        self._pattern_health_values[key] = (score, attributes)

        # Check alerts if metrics provided
        if metrics and session_id:
            alerting.check_pattern_health(metrics, session_id)

    def record_operator_flag(self, message_id: str, session_id: str | None = None):
        """
        Record a operator flag detection.

        Args:
            message_id: Unique message identifier
            session_id: Optional session identifier
        """
        attributes = {"message_id": message_id}
        if session_id:
            attributes["session_id"] = session_id
        self.operator_flags.add(1, attributes=attributes)

        # Send critical alert for operator flag
        if session_id:
            alerting.send_alert(
                AlertSeverity.CRITICAL,
                f"OPERATOR FLAG RECEIVED for message {message_id} in session {session_id}",
                {"message_id": message_id, "session_id": session_id},
            )

    def record_delivery_failure(
        self, message_id: str, session_id: str | None = None, reason: str | None = None
    ):
        """
        Record a delivery failure.

        Args:
            message_id: Unique message identifier
            session_id: Optional session identifier
            reason: Optional failure reason
        """
        attributes = {"message_id": message_id}
        if session_id:
            attributes["session_id"] = session_id
        if reason:
            attributes["reason"] = reason
        self.delivery_failures.add(1, attributes=attributes)

    def start_span(self, name: str, **attributes):
        """
        Start a new trace span.

        Args:
            name: Span name
            **attributes: Span attributes

        Returns:
            Span context manager
        """
        return self.tracer.start_as_current_span(name, attributes=attributes)


# Global telemetry instance - lazy initialization
_telemetry_instance: TelemetryService | None = None


def get_telemetry() -> TelemetryService:
    """
    Get or create global telemetry instance.

    Returns:
        TelemetryService instance

    Raises:
        ConfigurationError: If OpenTelemetry is not available
    """
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = TelemetryService()
    return _telemetry_instance


# For backward compatibility, provide a lazy getter
# Accessing telemetry.xxx will trigger initialization
class _LazyTelemetry:
    """Lazy wrapper for telemetry service."""

    def __getattr__(self, name: str):
        """Defer attribute access until telemetry is actually used."""
        return getattr(get_telemetry(), name)


# Global telemetry instance (lazy)
telemetry = _LazyTelemetry()
