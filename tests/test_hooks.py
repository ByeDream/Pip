"""Tests for the PreCompact / Stop hook callbacks.

We don't spin up a real SDK — hook callbacks are plain async functions that
take ``(input_data, tool_use_id, context)`` and mutate ``MemoryStore``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pip_agent.hooks import _pre_compact_hook, _stop_hook, build_hooks
from pip_agent.memory import MemoryStore


@pytest.fixture
def memory_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(base_dir=tmp_path / "agents", agent_id="pip-boy")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TestBuildHooks:
    def test_registers_precompact_and_stop_only(self, memory_store):
        hooks = build_hooks(memory_store=memory_store)
        assert set(hooks.keys()) == {"PreCompact", "Stop"}
        assert len(hooks["PreCompact"]) == 1
        assert len(hooks["Stop"]) == 1


class TestStopHook:
    def test_stamps_last_activity_at(self, memory_store):
        cb = _stop_hook(memory_store)
        _run(cb({}, None, None))
        state = memory_store.load_state()
        assert "last_activity_at" in state
        assert state["last_activity_at"] > 0

    def test_no_memory_store_is_noop(self):
        cb = _stop_hook(None)
        result = _run(cb({}, None, None))
        assert result == {}


class TestPreCompactHook:
    def test_missing_transcript_path_skips_reflect(self, memory_store):
        cb = _pre_compact_hook(memory_store)
        _run(cb({"session_id": "abc"}, None, None))
        state = memory_store.load_state()
        assert "last_pre_compact_at" in state
        assert state.get("last_pre_compact_session_id") == "abc"
        # No reflect should have run, so no offset map persisted.
        assert "last_reflect_jsonl_offset" not in state

    def test_missing_file_is_tolerated(self, memory_store, tmp_path):
        cb = _pre_compact_hook(memory_store)
        _run(cb({
            "session_id": "abc",
            "transcript_path": str(tmp_path / "missing.jsonl"),
        }, None, None))
        # Didn't crash; stamps are still recorded.
        state = memory_store.load_state()
        assert state["last_pre_compact_session_id"] == "abc"

    def test_triggers_reflect_and_advances_offset(self, memory_store, tmp_path):
        path = tmp_path / "session.jsonl"
        path.write_text(
            json.dumps({"role": "user", "content": "hello"}) + "\n"
            + json.dumps({"role": "assistant", "content": "hi"}) + "\n",
            encoding="utf-8",
        )

        # Stub reflect_from_jsonl so the hook doesn't try to hit an LLM.
        def fake_reflect(transcript_path, *, start_offset, agent_id, **kw):
            return 42, [{
                "ts": 1.0,
                "text": "user likes concise answers",
                "category": "preference",
                "source": "auto",
            }]

        with patch("pip_agent.memory.reflect.reflect_from_jsonl", side_effect=fake_reflect):
            cb = _pre_compact_hook(memory_store)
            _run(cb({
                "session_id": "sess-123",
                "transcript_path": str(path),
                "trigger": "manual",
            }, None, None))

        state = memory_store.load_state()
        assert state["last_reflect_jsonl_offset"]["sess-123"] == 42
        assert "last_reflect_at" in state
        # The observation was persisted.
        obs_dir = tmp_path / "agents" / "pip-boy" / "observations"
        assert any(obs_dir.glob("*.jsonl"))

    def test_reflect_failure_preserves_offset(self, memory_store, tmp_path):
        path = tmp_path / "session.jsonl"
        path.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")

        def raising_reflect(*a, **kw):
            raise RuntimeError("boom")

        with patch("pip_agent.memory.reflect.reflect_from_jsonl", side_effect=raising_reflect):
            cb = _pre_compact_hook(memory_store)
            _run(cb({
                "session_id": "sess-xyz",
                "transcript_path": str(path),
            }, None, None))

        state = memory_store.load_state()
        assert "last_reflect_jsonl_offset" not in state

    def test_no_memory_store_is_noop(self, tmp_path):
        cb = _pre_compact_hook(None)
        result = _run(cb({"transcript_path": "x"}, None, None))
        assert result == {}
