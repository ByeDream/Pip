"""Tests for pure-logic memory helpers (tokenize, temporal_decay, search_memories).

Pipeline-level integration tests (reflect over CC JSONL, scheduler-driven dream,
etc.) are rewritten in Phase 4.5 and Phase 11; see ``docs/sdk-contract-notes.md``
for the new data-source contract.
"""

from __future__ import annotations

import time

import pytest

from pip_agent.memory.recall import search_memories, temporal_decay, tokenize


class TestTokenize:
    def test_english_words(self):
        tokens = tokenize("Hello World 123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens

    def test_cjk_characters(self):
        tokens = tokenize("测试中文")
        assert "测" in tokens
        assert "试" in tokens

    def test_mixed(self):
        tokens = tokenize("Hello 世界 test")
        assert "hello" in tokens
        assert "世" in tokens
        assert "界" in tokens
        assert "test" in tokens

    def test_empty(self):
        assert tokenize("") == []


class TestTemporalDecay:
    def test_recent_is_near_one(self):
        assert temporal_decay(time.time(), half_life_days=30.0) == pytest.approx(1.0, abs=0.01)

    def test_old_is_less(self):
        thirty_days_ago = time.time() - 30 * 86400
        val = temporal_decay(thirty_days_ago, half_life_days=30.0)
        assert 0.4 < val < 0.6

    def test_future_returns_one(self):
        assert temporal_decay(time.time() + 86400) == 1.0


class TestSearchMemories:
    def test_empty_input(self):
        assert search_memories("anything", []) == []

    def test_basic_match(self):
        memories = [
            {"text": "user likes python", "tags": [], "updated_at": time.time()},
            {"text": "user prefers coffee", "tags": [], "updated_at": time.time()},
        ]
        results = search_memories("python", memories, top_k=5)
        assert len(results) >= 1
        assert "python" in results[0]["text"].lower()
