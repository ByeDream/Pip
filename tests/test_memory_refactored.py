"""Tests for MemoryStore read/write and the ``extract_json_array`` utility.

The full reflect + consolidate + dream integration suite is rewritten in
Phase 4.5 (data source migrated to Claude Code JSONL) and Phase 11.
"""

from __future__ import annotations

import json
from pathlib import Path

from pip_agent.memory import MemoryStore
from pip_agent.memory.utils import extract_json_array


class TestExtractJsonArray:
    def test_plain_json(self):
        assert extract_json_array('[{"a": 1}]') == [{"a": 1}]

    def test_markdown_fenced(self):
        text = '```json\n[{"a": 1}]\n```'
        assert extract_json_array(text) == [{"a": 1}]

    def test_empty_array(self):
        assert extract_json_array("[]") == []

    def test_invalid_returns_none(self):
        assert extract_json_array("not json at all") is None


class TestMemoryStoreBasics:
    def test_write_observation_appends_to_jsonl(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        store.write_single("first observation", category="observation", source="user")
        observations = list((tmp_path / "agents" / "pip-boy" / "observations").glob("*.jsonl"))
        assert len(observations) == 1
        lines = [
            json.loads(line)
            for line in observations[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["text"] == "first observation"

    def test_load_state_missing_returns_empty_dict(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        assert store.load_state() == {}

    def test_save_and_load_state_roundtrip(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        store.save_state({"last_reflect_at": 12345})
        assert store.load_state() == {"last_reflect_at": 12345}
