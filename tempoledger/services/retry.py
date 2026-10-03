"""Retry logic with exponential backoff and jitter."""

import asyncio
import logging
import random
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tempoledger.exceptions import DatabaseError, SMSError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Retryable exception types
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    DatabaseError,
    SMSError,
)


def add_jitter(value: float, jitter_factor: float = 0.1) -> float:
    """Add jitter to a value to avoid thundering herd.

    Args:
        value: Base value to add jitter to
        jitter_factor: Factor to apply jitter (default 0.1 = 10%)

    Returns:
        Value with jitter added
    """
    jitter = value * jitter_factor * (random.random() * 2 - 1)
    return max(0, value + jitter)


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = RETRYABLE_EXCEPTIONS,
):
    """Decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        jitter: Whether to add jitter to delays
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            delay = initial_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait_time = min(delay, max_delay)
                        if jitter:
                            wait_time = add_jitter(wait_time)
                        logger.warning(
                            f"Retrying {func.__name__} after error (attempt {attempt}/{max_attempts}): {e}",
                            extra={"function": func.__name__, "error": str(e), "attempt": attempt},
                        )
                        await asyncio.sleep(wait_time)
                        delay *= exponential_base
                    else:
                        logger.error(
                            f"Max retries reached for {func.__name__}: {e}",
                            extra={"function": func.__name__, "error": str(e)},
                            exc_info=True,
                        )
                except Exception as e:
                    logger.error(
                        f"Non-retryable error in {func.__name__}: {e}",
                        extra={"function": func.__name__, "error": str(e)},
                        exc_info=True,
                    )
                    raise

            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def retry_sync(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = RETRYABLE_EXCEPTIONS,
):
    """Decorator for retrying sync functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(
                multiplier=initial_delay,
                max=max_delay,
                exp_base=exponential_base,
            ),
            retry=retry_if_exception_type(retryable_exceptions),
            reraise=True,
        )
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                logger.warning(
                    f"Retrying {func.__name__} after error: {e}",
                    extra={"function": func.__name__, "error": str(e)},
                )
                raise
            except Exception as e:
                logger.error(
                    f"Non-retryable error in {func.__name__}: {e}",
                    extra={"function": func.__name__, "error": str(e)},
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator


# Pre-configured retry decorators for common use cases
retry_database = retry_with_backoff(
    max_attempts=3,
    initial_delay=0.5,
    max_delay=10.0,
    retryable_exceptions=(DatabaseError, ConnectionError, TimeoutError),
)

retry_sms = retry_with_backoff(
    max_attempts=3,
    initial_delay=1.0,
    max_delay=30.0,
    retryable_exceptions=(SMSError, ConnectionError, TimeoutError),
)

retry_network = retry_with_backoff(
    max_attempts=5,
    initial_delay=1.0,
    max_delay=60.0,
    retryable_exceptions=(ConnectionError, TimeoutError, asyncio.TimeoutError),
)
