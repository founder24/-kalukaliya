"""
Circuit Breaker Tests: Resilience Pattern Validation
"""

import pytest
import asyncio
from app.core.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerError


class TestCircuitBreaker:
    """Test circuit breaker pattern implementation"""

    @pytest.mark.asyncio
    async def test_closed_state_allows_calls(self):
        """Test that closed circuit allows all calls"""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def successful_func():
            return "success"

        result = await cb.call(successful_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self):
        """Test that circuit opens after reaching failure threshold"""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def failing_func():
            raise Exception("Service unavailable")

        # Trigger failures
        for i in range(3):
            try:
                await cb.call(failing_func)
            except Exception:
                pass

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self):
        """Test that open circuit rejects calls immediately"""
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=60)

        async def failing_func():
            raise Exception("Service unavailable")

        # Open the circuit
        for i in range(2):
            try:
                await cb.call(failing_func)
            except Exception:
                pass

        # Should raise CircuitBreakerError immediately
        with pytest.raises(CircuitBreakerError):
            await cb.call(failing_func)

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        """Test that circuit transitions to half-open after timeout"""
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=1)

        async def failing_func():
            raise Exception("Service unavailable")

        # Open the circuit
        for i in range(2):
            try:
                await cb.call(failing_func)
            except Exception:
                pass

        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        await asyncio.sleep(1.1)

        # Should transition to HALF_OPEN on next state check
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_recovery_on_success_in_half_open(self):
        """Test that circuit closes after successful calls in half-open"""
        cb = CircuitBreaker(
            name="test",
            failure_threshold=2,
            reset_timeout=1,
            success_threshold=2,
            half_open_max_calls=3,  # Allow more calls in half-open for testing
        )

        async def failing_func():
            raise Exception("Service unavailable")

        async def successful_func():
            return "success"

        # Open the circuit
        for i in range(2):
            try:
                await cb.call(failing_func)
            except Exception:
                pass

        # Wait for timeout
        await asyncio.sleep(1.1)

        # Successful calls in half-open should close circuit
        result1 = await cb.call(successful_func)
        result2 = await cb.call(successful_func)

        assert result1 == "success"
        assert result2 == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_status_reporting(self):
        """Test circuit breaker status reporting"""
        cb = CircuitBreaker(name="test", failure_threshold=5)

        status = cb.get_status()

        assert status["name"] == "test"
        assert status["state"] == "CLOSED"
        assert status["failure_count"] == 0
        assert status["failure_threshold"] == 5
