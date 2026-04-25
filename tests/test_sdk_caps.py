"""Process-global SDK capability cache (``pip_agent.sdk_caps``)."""
from __future__ import annotations

import pytest

from pip_agent import sdk_caps


@pytest.fixture(autouse=True)
def _clear_caps():
    sdk_caps.reset_for_test()
    yield
    sdk_caps.reset_for_test()


class TestRecord:
    def test_first_non_empty_call_caches(self) -> None:
        sdk_caps.record(["compact", "context", "cost"])
        assert sdk_caps.get() == {"compact", "context", "cost"}

    def test_subsequent_calls_are_ignored(self) -> None:
        # First observation wins; a degenerate later init must not
        # shrink or replace the canonical list.
        sdk_caps.record(["compact", "context"])
        sdk_caps.record(["only-this"])
        assert sdk_caps.get() == {"compact", "context"}

    def test_strips_leading_slash_and_lowercases(self) -> None:
        sdk_caps.record(["/Compact", "/CONTEXT", "  /cost  "])
        assert sdk_caps.get() == {"compact", "context", "cost"}

    def test_ignores_empty_iterable(self) -> None:
        sdk_caps.record([])
        assert sdk_caps.get() is None

    def test_ignores_none(self) -> None:
        sdk_caps.record(None)
        assert sdk_caps.get() is None

    def test_ignores_non_string_entries(self) -> None:
        sdk_caps.record(["compact", 42, None, {"a": 1}, "context"])
        assert sdk_caps.get() == {"compact", "context"}

    def test_skips_blank_after_strip(self) -> None:
        sdk_caps.record(["", "/", "   ", "/compact"])
        assert sdk_caps.get() == {"compact"}


class TestGet:
    def test_returns_none_before_first_record(self) -> None:
        assert sdk_caps.get() is None

    def test_returns_copy_so_mutation_is_safe(self) -> None:
        sdk_caps.record(["compact"])
        snapshot = sdk_caps.get()
        assert snapshot is not None
        snapshot.add("hacked")
        assert sdk_caps.get() == {"compact"}
