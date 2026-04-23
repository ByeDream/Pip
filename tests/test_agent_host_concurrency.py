"""Concurrency-field contract test for :class:`AgentHost`.

Locks in the Plan B semaphore-placement semantics (see the block comment
around ``_one_shot_semaphore`` in ``AgentHost.__init__``):

* The per-session lock still guards same-session serialisation.
* The historical global ``_semaphore`` / ``_max_concurrent`` pair has been
  renamed and narrowed: ``_one_shot_semaphore`` only wraps the fallback
  ``run_query`` branch (cron / heartbeat / non-streaming), so streaming
  turns are NOT bottlenecked by it.
* Streaming-turn spawn throttling is the job of ``_streaming_lock`` +
  ``settings.stream_max_live``, not this semaphore.

These assertions are structural — they catch silent regressions where
someone re-adds the old ``_semaphore`` wrap around the streaming path,
or renames ``_one_shot_semaphore`` back to the ambiguous old name.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

from pip_agent.agent_host import AgentHost


def _make_host() -> AgentHost:
    """Construct an ``AgentHost`` with zero-dependency stubs.

    ``_load_sessions`` gracefully returns ``{}`` when no session-store
    file exists, so the only heavy collaborators that need mocking are
    the three ctor arguments. Everything else is pure attribute init.
    """
    return AgentHost(
        registry=MagicMock(),
        binding_table=MagicMock(),
        channel_mgr=MagicMock(),
        scheduler=None,
    )


class TestConcurrencyFields:
    def test_one_shot_semaphore_exists_with_expected_capacity(self) -> None:
        host = _make_host()
        assert isinstance(host._one_shot_semaphore, asyncio.Semaphore)
        # Default cap is 3 — matches the old ``_max_concurrent`` value so
        # the one-shot path keeps the same RAM-burst ceiling post-split.
        assert host._one_shot_max_concurrent == 3
        # ``asyncio.Semaphore`` exposes the free-slot count via ``_value``.
        assert host._one_shot_semaphore._value == 3

    def test_old_semaphore_names_are_gone(self) -> None:
        """Guards against accidental revert to the ambiguous old name."""
        host = _make_host()
        assert not hasattr(host, "_semaphore"), (
            "Found legacy ``_semaphore`` attribute — Plan B renamed it to "
            "``_one_shot_semaphore`` to make its narrowed semantic explicit."
        )
        assert not hasattr(host, "_max_concurrent"), (
            "Found legacy ``_max_concurrent`` attribute — Plan B renamed it "
            "to ``_one_shot_max_concurrent``."
        )

    def test_session_lock_map_is_still_per_session(self) -> None:
        """Per-session serialisation is orthogonal to Plan B; must stay."""
        host = _make_host()
        assert host._session_locks == {}
        # Accessor creates on demand and returns the SAME lock for the
        # same key (canonical contract for group-chat same-session
        # serialisation).
        lock_a1 = host._get_session_lock("sk-a")
        lock_a2 = host._get_session_lock("sk-a")
        lock_b = host._get_session_lock("sk-b")
        assert lock_a1 is lock_a2
        assert lock_a1 is not lock_b


class TestSemaphorePlacementInSource:
    """Source-level assertions that pin down where the semaphore is acquired.

    Structural in nature: we inspect the raw source of the two relevant
    methods. If a future edit re-introduces the old wide wrap (streaming
    turns bottlenecking on ``_one_shot_semaphore``), these will fail
    loudly instead of silently regressing perf.
    """

    def test_execute_turn_body_does_not_wrap_streaming_with_semaphore(
        self,
    ) -> None:
        src = inspect.getsource(AgentHost._execute_turn_body)
        # The outer lock scope must NOT list the semaphore alongside the
        # per-session lock. Plan B moved the wrap down to the one-shot
        # branch only.
        assert "self._get_session_lock(sk), self._one_shot_semaphore" not in src
        assert "self._get_session_lock(sk), self._semaphore" not in src
        assert "async with self._get_session_lock(sk):" in src

    def test_execute_turn_body_wraps_one_shot_run_query_with_semaphore(
        self,
    ) -> None:
        src = inspect.getsource(AgentHost._execute_turn_body)
        assert "self._one_shot_semaphore" in src, (
            "One-shot ``run_query`` branch must stay wrapped by "
            "``_one_shot_semaphore`` — removing it would let cron/"
            "heartbeat spikes spawn unbounded CC subprocesses."
        )

    def test_streaming_turn_runner_does_not_reacquire_one_shot_semaphore(
        self,
    ) -> None:
        """Streaming turns operate on already-spawned long-lived subprocesses.

        They must never take ``_one_shot_semaphore`` — the whole point of
        Plan B is that streaming turns flow in parallel across sessions.
        """
        src = inspect.getsource(AgentHost._run_turn_streaming)
        assert "_one_shot_semaphore" not in src
        assert "_semaphore" not in src
