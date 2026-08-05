"""Tests for the AI outage alert de-duplication and concurrency safety.

These tests cover:
- Dedup: alert fires once per 10-minute window, not on every failing request
- Concurrency: concurrent coroutines cannot both bypass the dedup gate
- Non-streaming path: call_llm() triggers the alert on dual failure
- State reset: window resets after 10 minutes
"""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_alert_module():
    """Reload the alert module so each test starts with fresh in-memory state."""
    mod_name = "app.services.comms.ai_outage_alert"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------

class TestAiOutageAlertDedup:
    """record_ai_outage fires at most one email per 10-minute window."""

    @pytest.mark.asyncio
    async def test_first_call_sends_alert(self):
        """First call with both providers down should send exactly one alert."""
        mod = _reload_alert_module()

        with patch.object(mod, "_send_outage_alert", new_callable=AsyncMock, return_value=True) as mock_send:
            await mod.record_ai_outage("user1", sarvam_error="sarvam down", gemini_error="gemini 403")
            assert mock_send.call_count == 1

    @pytest.mark.asyncio
    async def test_second_call_within_window_does_not_send(self):
        """Second call within 10 minutes must not trigger another email."""
        mod = _reload_alert_module()

        with patch.object(mod, "_send_outage_alert", new_callable=AsyncMock, return_value=True) as mock_send:
            await mod.record_ai_outage("user1", sarvam_error="err", gemini_error="err")
            await mod.record_ai_outage("user2", sarvam_error="err", gemini_error="err")
            assert mock_send.call_count == 1  # still one

    @pytest.mark.asyncio
    async def test_call_after_window_sends_new_alert(self):
        """After the 10-minute window expires a new alert should be sent."""
        mod = _reload_alert_module()

        with patch.object(mod, "_send_outage_alert", new_callable=AsyncMock, return_value=True) as mock_send:
            # First alert
            await mod.record_ai_outage("user1", sarvam_error="err", gemini_error="err")
            assert mock_send.call_count == 1

            # Advance state: pretend 11 minutes have passed
            mod._last_alert_sent_at -= (mod._DEDUP_WINDOW_SECONDS + 60)
            mod._window_start -= (mod._DEDUP_WINDOW_SECONDS + 60)

            # Second alert (new window)
            await mod.record_ai_outage("user2", sarvam_error="err2", gemini_error="err2")
            assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_failed_send_resets_timestamp_so_retry_fires(self):
        """If _send_outage_alert returns False the timestamp is reset so the next
        call will attempt to send again instead of waiting 10 minutes."""
        mod = _reload_alert_module()

        with patch.object(mod, "_send_outage_alert", new_callable=AsyncMock, return_value=False) as mock_send:
            await mod.record_ai_outage("user1", sarvam_error="err", gemini_error="err")
            # Timestamp should be reset to 0
            assert mod._last_alert_sent_at == 0.0

        # Next call should attempt again
        with patch.object(mod, "_send_outage_alert", new_callable=AsyncMock, return_value=True) as mock_send2:
            await mod.record_ai_outage("user1", sarvam_error="err", gemini_error="err")
            assert mock_send2.call_count == 1

    @pytest.mark.asyncio
    async def test_affected_users_accumulated_across_calls(self):
        """All user IDs within a window should be counted in the alert."""
        mod = _reload_alert_module()

        captured_counts = []

        async def fake_send(affected_count, sarvam_errors, gemini_errors):
            captured_counts.append(affected_count)
            return True

        with patch.object(mod, "_send_outage_alert", new=fake_send):
            # First call fires the alert with 1 user
            await mod.record_ai_outage("user1", sarvam_error="e", gemini_error="e")
            # Second call accumulates but does not fire
            await mod.record_ai_outage("user2", sarvam_error="e", gemini_error="e")

        assert captured_counts == [1]  # only one send happened
        # Both users are in the set
        assert len(mod._affected_user_ids) == 2


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class TestAiOutageAlertConcurrency:
    """Concurrent coroutines cannot both bypass the dedup gate."""

    @pytest.mark.asyncio
    async def test_concurrent_calls_send_only_one_email(self):
        """Multiple simultaneous outage events must result in exactly one email."""
        mod = _reload_alert_module()

        send_count = 0
        barrier = asyncio.Event()  # ensures all coroutines reach the lock together

        async def slow_send(affected_count, sarvam_errors, gemini_errors):
            nonlocal send_count
            send_count += 1
            await asyncio.sleep(0)  # yield to let others run
            return True

        with patch.object(mod, "_send_outage_alert", new=slow_send):
            # Launch 10 concurrent outage events
            await asyncio.gather(
                *[
                    mod.record_ai_outage(f"user{i}", sarvam_error="err", gemini_error="err")
                    for i in range(10)
                ]
            )

        assert send_count == 1, (
            f"Expected exactly 1 alert email, but {send_count} were sent "
            "(dedup lock is not working correctly under concurrency)"
        )


# ---------------------------------------------------------------------------
# Non-streaming path (call_llm) wiring test
# ---------------------------------------------------------------------------

class TestCallLlmAlertWiring:
    """call_llm() fires the outage alert when both providers fail."""

    @pytest.mark.asyncio
    async def test_call_llm_triggers_outage_alert_on_dual_failure(self):
        """When Sarvam raises and Gemini also raises, record_ai_outage is called."""
        from unittest.mock import AsyncMock, patch

        # Patch generate_response (Sarvam path) to raise
        sarvam_exc = RuntimeError("sarvam 503")
        # Patch generate_gemini (Gemini path) to raise
        gemini_exc = RuntimeError("gemini 403 quota exceeded")

        record_mock = AsyncMock()

        with (
            patch("app.services.ai.router.generate_response", side_effect=sarvam_exc),
            patch(
                "app.services.ai.gemini_fallback.generate_gemini",
                side_effect=gemini_exc,
            ),
            patch(
                "app.services.ai.gemini_fallback._available",
                return_value=True,
            ),
            patch(
                "app.services.comms.ai_outage_alert.record_ai_outage",
                record_mock,
            ),
        ):
            from app.services.chat_service import ChatService

            with pytest.raises(RuntimeError):
                await ChatService.call_llm(
                    system_prompt="sys",
                    sanitized_message="hello",
                    target_model="sarvam-105b",
                    detected_lang="en",
                    user_id="test-user",
                )

        record_mock.assert_awaited_once()
        call_kwargs = record_mock.call_args.kwargs
        assert call_kwargs["user_id"] == "test-user"
        assert "sarvam" in call_kwargs.get("sarvam_error", "").lower() or "503" in call_kwargs.get("sarvam_error", "")
        assert call_kwargs.get("gemini_error") is not None


# ---------------------------------------------------------------------------
# Streaming path wiring test
# ---------------------------------------------------------------------------

class TestStreamLlmAlertWiring:
    """stream_llm() stores dead-letter with both_providers_down=True on dual failure."""

    @pytest.mark.asyncio
    async def test_stream_llm_stores_dead_letter_with_both_providers_down(self):
        """When Sarvam stream fails and Gemini stream fails, dead_letter is stored
        with both_providers_down=True so the alert is triggered via dead_letter."""
        sarvam_exc = RuntimeError("sarvam circuit open")
        gemini_exc = RuntimeError("gemini stream error")

        store_mock = AsyncMock()

        with (
            patch("app.services.ai.router.stream_response", side_effect=sarvam_exc),
            patch(
                "app.services.ai.gemini_fallback.stream_gemini",
                side_effect=gemini_exc,
            ),
            patch(
                "app.services.ai.gemini_fallback._available",
                return_value=True,
            ),
            patch(
                "app.services.dead_letter.store_dead_letter",
                store_mock,
            ),
        ):
            from app.services.chat_service import ChatService

            chunks = []
            async for chunk in ChatService.stream_llm(
                system_prompt="sys",
                sanitized_message="hello",
                target_model="sarvam-105b",
                detected_lang="en",
                user_id="stream-user",
                request_message="hello",
            ):
                chunks.append(chunk)

        store_mock.assert_awaited_once()
        call_kwargs = store_mock.call_args.kwargs
        assert call_kwargs.get("both_providers_down") is True
        assert call_kwargs.get("gemini_error") is not None
