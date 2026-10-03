"""Custom exceptions for the Tempoledger system."""


class TempoledgerError(Exception):
    """Base exception for Tempoledger errors."""

    pass


class SchedulingError(TempoledgerError):
    """Base exception for scheduling errors."""

    pass


class PatternAnomalyError(SchedulingError):
    """Raised when pattern detection fails."""

    pass


class MessageQueueError(SchedulingError):
    """Raised when message queue operations fail."""

    pass


class DatabaseError(TempoledgerError):
    """Raised when database operations fail."""

    pass


class SMSError(TempoledgerError):
    """Raised when SMS operations fail."""

    pass


class AgentError(TempoledgerError):
    """Raised when agent operations fail."""

    pass


class ConfigurationError(TempoledgerError):
    """Raised when configuration is invalid."""

    pass


class RateLimitError(TempoledgerError):
    """Raised when rate limit is exceeded."""

    pass


class CircuitBreakerError(TempoledgerError):
    """Raised when circuit breaker is open."""

    pass


class ValidationError(TempoledgerError):
    """Raised when validation fails."""

    pass
