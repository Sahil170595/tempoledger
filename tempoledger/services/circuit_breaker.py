"""Circuit breaker implementation for external services."""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import pybreaker

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Circuit breaker for database operations
db_breaker = pybreaker.CircuitBreaker(
    fail_max=5,  # Open after 5 failures
    reset_timeout=60,  # Half-open after 60 seconds
    name="database",
)

# Circuit breaker for SMS operations
sms_breaker = pybreaker.CircuitBreaker(
    fail_max=5,  # Open after 5 failures
    reset_timeout=60,  # Half-open after 60 seconds
    name="sms",
)


def circuit_breaker(breaker: pybreaker.CircuitBreaker):
    """Decorator for wrapping async functions with circuit breaker.

    Args:
        breaker: Circuit breaker instance

    Returns:
        Decorated function with circuit breaker protection
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await breaker.call_async(func, *args, **kwargs)
            except pybreaker.CircuitBreakerError as e:
                logger.error(
                    f"Circuit breaker open for {breaker.name}: {e}",
                    extra={"breaker": breaker.name, "state": str(breaker.current_state)},
                )
                raise
            except Exception as e:
                logger.error(
                    f"Error in {func.__name__} with circuit breaker: {e}",
                    extra={"breaker": breaker.name, "function": func.__name__},
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator


def circuit_breaker_sync(breaker: pybreaker.CircuitBreaker):
    """Decorator for wrapping sync functions with circuit breaker.

    Args:
        breaker: Circuit breaker instance

    Returns:
        Decorated function with circuit breaker protection
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return breaker.call(func, *args, **kwargs)
            except pybreaker.CircuitBreakerError as e:
                logger.error(
                    f"Circuit breaker open for {breaker.name}: {e}",
                    extra={"breaker": breaker.name, "state": str(breaker.current_state)},
                )
                raise
            except Exception as e:
                logger.error(
                    f"Error in {func.__name__} with circuit breaker: {e}",
                    extra={"breaker": breaker.name, "function": func.__name__},
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator


# Pre-configured decorators
circuit_breaker_db = circuit_breaker(db_breaker)
circuit_breaker_sms = circuit_breaker(sms_breaker)
