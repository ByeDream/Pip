"""Tests for reliable multi-channel delivery (``send_with_retry``).

Cron-service delivery tests live with the rewritten ``host_scheduler.py``
(Phase 5 / Phase 11).
"""

from __future__ import annotations

from unittest.mock import patch

from pip_agent.channels import BACKOFF_SCHEDULE, Channel, CLIChannel, send_with_retry


class _MockChannel(Channel):
    """Channel that fails a configurable number of times then succeeds."""

    name = "test"

    def __init__(self, fail_count: int = 0):
        self._fail_count = fail_count
        self._attempts = 0

    def send(self, to: str, text: str, **kw) -> bool:
        self._attempts += 1
        if self._attempts <= self._fail_count:
            return False
        return True


class TestSendWithRetry:
    def test_success_on_first_try(self):
        ch = _MockChannel(fail_count=0)
        assert send_with_retry(ch, "user", "hello") is True
        assert ch._attempts == 1

    @patch("pip_agent.channels.time.sleep")
    def test_retries_on_failure(self, mock_sleep):
        ch = _MockChannel(fail_count=2)
        assert send_with_retry(ch, "user", "hello") is True
        assert ch._attempts == 3
        assert mock_sleep.call_count == 2

    @patch("pip_agent.channels.time.sleep")
    def test_gives_up_after_max_retries(self, mock_sleep):
        ch = _MockChannel(fail_count=100)
        assert send_with_retry(ch, "user", "hello") is False
        assert ch._attempts == 1 + len(BACKOFF_SCHEDULE)

    def test_cli_skips_chunking_and_retry(self, capsys):
        ch = CLIChannel()
        assert send_with_retry(ch, "cli-user", "hello") is True
        captured = capsys.readouterr()
        assert "hello" in captured.out

    @patch("pip_agent.channels.time.sleep")
    def test_chunks_long_text(self, mock_sleep):
        ch = _MockChannel(fail_count=0)
        ch.name = "wechat"
        text = "A" * 3000
        assert send_with_retry(ch, "user", text) is True
        assert ch._attempts >= 2

    @patch("pip_agent.channels.time.sleep")
    def test_partial_failure(self, mock_sleep):
        """If one chunk fails permanently, returns False but still sends others."""
        class _PartialChannel(Channel):
            name = "test"

            def send(self, to: str, text: str, **kw) -> bool:
                if "FAIL" in text:
                    return False
                return True

        ch = _PartialChannel()
        text = "OK chunk\n\n" + "FAIL" * 500
        result = send_with_retry(ch, "user", text)
        assert result is False
