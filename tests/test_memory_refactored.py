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


class TestPurgeObservations:
    """H5 regression guard: Dream must be able to hard-delete consumed
    observations so consolidate doesn't re-weight the same batch every
    night. Purge is cutoff-based so an observation written while Dream
    is running (ts > cutoff) survives.
    """

    def _seed(self, store: MemoryStore, rows: list[dict]) -> None:
        for obs in rows:
            store.write_observations([obs])

    def test_deletes_lines_at_or_before_cutoff(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        self._seed(store, [
            {"ts": 1.0, "text": "old 1", "category": "decision", "source": "auto"},
            {"ts": 2.0, "text": "old 2", "category": "decision", "source": "auto"},
            {"ts": 3.0, "text": "new", "category": "decision", "source": "auto"},
        ])

        purged = store.purge_observations_through(2.0)
        assert purged == 2
        remaining = store.load_all_observations()
        assert [o["text"] for o in remaining] == ["new"]

    def test_empty_file_is_unlinked(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        self._seed(store, [
            {"ts": 1.0, "text": "dropped", "category": "decision", "source": "auto"},
        ])
        obs_dir = tmp_path / "agents" / "pip-boy" / "observations"
        files_before = list(obs_dir.glob("*.jsonl"))
        assert files_before

        store.purge_observations_through(10.0)
        assert not list(obs_dir.glob("*.jsonl"))

    def test_keeps_unparseable_lines(self, tmp_path: Path):
        """Malformed JSON lines are held back from purge — destroying
        bytes we can't even decode is worse than keeping noise."""
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        obs_dir = tmp_path / "agents" / "pip-boy" / "observations"
        obs_dir.mkdir(parents=True, exist_ok=True)
        fp = obs_dir / "2025-01-01.jsonl"
        fp.write_text(
            '{"ts": 1.0, "text": "old", "category": "x", "source": "auto"}\n'
            "not json at all\n"
            '{"ts": 3.0, "text": "new", "category": "x", "source": "auto"}\n',
            encoding="utf-8",
        )

        purged = store.purge_observations_through(2.0)
        assert purged == 1
        remaining_raw = fp.read_text(encoding="utf-8")
        assert "not json at all" in remaining_raw
        assert "old" not in remaining_raw

    def test_cutoff_before_all_obs_keeps_everything(self, tmp_path: Path):
        """Mid-Dream race: if reflect wrote observations between the
        Dream started_at capture and the purge, their ts > cutoff and
        they survive intact.
        """
        store = MemoryStore(tmp_path / "agents", "pip-boy")
        self._seed(store, [
            {"ts": 10.0, "text": "a", "category": "x", "source": "auto"},
            {"ts": 11.0, "text": "b", "category": "x", "source": "auto"},
        ])
        purged = store.purge_observations_through(5.0)
        assert purged == 0
        assert len(store.load_all_observations()) == 2
