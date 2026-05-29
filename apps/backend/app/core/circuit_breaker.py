"""
Circuit Breaker Pattern for AI Provider Resilience
Prevents cascading failures when external services are unavailable
"""

import time
from typing import Any, Callable, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"  # Normal operation
    OPEN = "OPEN"  # Failing fast
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""

    pass


class CircuitBreaker:
    """
    Circuit Breaker implementation for AI provider calls.

    Features:
    - Automatic state transitions based on failure threshold
    - Configurable reset timeout
    - Half-open state for recovery testing
    - Thread-safe state management
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        reset_timeout: int = 60,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0

        # HF-101: asyncio.Lock for state transitions
        import asyncio
        self._state_lock = asyncio.Lock()

        # HF-119: Rate limiting for state transition logs
        self._last_state_log_time = 0.0

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for automatic transition from OPEN to HALF_OPEN"""
        if self._state == CircuitState.OPEN:
            if (
                self._last_failure_time
                and (time.time() - self._last_failure_time) >= self.reset_timeout
            ):
                logger.info(
                    f"Circuit '{self.name}' transitioning from OPEN to HALF_OPEN"
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute function through circuit breaker.

        Args:
            func: Async function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from func

        Raises:
            CircuitBreakerError: If circuit is open
        """
        async with self._state_lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                now = time.time()
                if now - self._last_state_log_time > 60:
                    self._last_state_log_time = now
                    logger.warning(
                        f"Circuit '{self.name}' is OPEN. Failing fast. "
                        f"Will retry after {self.reset_timeout}s"
                    )
                raise CircuitBreakerError(
                    f"Service {self.name} is unavailable (circuit open)"
                )

            if current_state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    now = time.time()
                    if now - self._last_state_log_time > 60:
                        self._last_state_log_time = now
                        logger.warning(f"Circuit '{self.name}' HALF_OPEN max calls reached")
                    raise CircuitBreakerError(
                        f"Service {self.name} is being tested (half-open limit reached)"
                    )
                self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        """Handle successful call"""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                now = time.time()
                if now - self._last_state_log_time > 60:
                    self._last_state_log_time = now
                    logger.info(f"Circuit '{self.name}' recovered. Transitioning to CLOSED")
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        else:
            # Reset failure count on success in CLOSED state
            self._failure_count = 0

    def _on_failure(self):
        """Handle failed call"""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            now = time.time()
            if now - self._last_state_log_time > 60:
                self._last_state_log_time = now
                logger.warning(
                    f"Circuit '{self.name}' failed in HALF_OPEN. Transitioning back to OPEN"
                )
            self._state = CircuitState.OPEN
            self._success_count = 0
        elif self._failure_count >= self.failure_threshold:
            now = time.time()
            if now - self._last_state_log_time > 60:
                self._last_state_log_time = now
                logger.warning(
                    f"Circuit '{self.name}' failure threshold reached ({self._failure_count}). "
                    f"Transitioning to OPEN"
                )
            self._state = CircuitState.OPEN

    def get_status(self) -> dict:
        """Get circuit breaker status for monitoring"""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "last_failure_time": self._last_failure_time,
        }


# Pre-configured circuit breakers for each AI provider
vertex_circuit_breaker = CircuitBreaker(
    name="Vertex AI", failure_threshold=5, reset_timeout=30
)

sarvam_circuit_breaker = CircuitBreaker(
    name="Sarvam AI", failure_threshold=5, reset_timeout=60
)

azure_search_circuit_breaker = CircuitBreaker(
    name="Azure Search", failure_threshold=3, reset_timeout=30
)
