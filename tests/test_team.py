from __future__ import annotations

import time
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pip_agent.profiler import Profiler
from pip_agent.team import (
    Bus,
    TeamManager,
    Teammate,
    TeammateSpec,
    VALID_MSG_TYPES,
    _parse_frontmatter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_MD = """\
---
name: alice
description: "Python backend developer."
model: test-model
max_turns: 5
tools: [bash, read, write]
---

You are alice, a Python backend developer.
"""

MINIMAL_MD = """\
---
name: bob
description: "Helper bot."
---

You are bob.
"""

NO_FRONTMATTER_MD = "Just a plain body with no YAML."


def _write_md(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(
    name: str, tool_input: dict, block_id: str = "tu_1",
) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _make_response(
    content: list, stop_reason: str = "end_turn",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _make_mgr(tmp_path, *names):
    """Create a TeamManager with the given teammate .md files."""
    user_dir = tmp_path / "user"
    mds = {"alice": SAMPLE_MD, "bob": MINIMAL_MD}
    for name in names:
        _write_md(user_dir, name, mds.get(name, SAMPLE_MD))
    return TeamManager(
        tmp_path / "builtin", user_dir, MagicMock(), Profiler(),
    )


# ---------------------------------------------------------------------------
# TeammateSpec
# ---------------------------------------------------------------------------


class TestTeammateSpec:
    def test_parse_full(self, tmp_path):
        path = _write_md(tmp_path, "alice", SAMPLE_MD)
        spec = TeammateSpec.from_file(path)
        assert spec.name == "alice"
        assert spec.description == "Python backend developer."
        assert spec.model == "test-model"
        assert spec.max_turns == 5
        assert spec.tools == ["bash", "read", "write"]
        assert "Python backend developer" in spec.system_body

    def test_parse_minimal_uses_defaults(self, tmp_path):
        path = _write_md(tmp_path, "bob", MINIMAL_MD)
        with patch("pip_agent.team.settings") as mock_settings:
            mock_settings.model = "default-model"
            mock_settings.subagent_max_rounds = 15
            spec = TeammateSpec.from_file(path)
        assert spec.name == "bob"
        assert spec.model == "default-model"
        assert spec.max_turns == 15
        assert spec.tools == [
            "bash", "read", "write", "edit", "glob", "web_search", "web_fetch",
        ]

    def test_no_frontmatter_uses_filename(self, tmp_path):
        path = _write_md(tmp_path, "charlie", NO_FRONTMATTER_MD)
        with patch("pip_agent.team.settings") as mock_settings:
            mock_settings.model = "m"
            mock_settings.subagent_max_rounds = 10
            spec = TeammateSpec.from_file(path)
        assert spec.name == "charlie"
        assert spec.system_body == NO_FRONTMATTER_MD

    def test_tools_as_csv_string(self, tmp_path):
        md = "---\nname: d\ndescription: d\ntools: bash, read\n---\nbody"
        path = _write_md(tmp_path, "d", md)
        with patch("pip_agent.team.settings") as ms:
            ms.model = "m"
            ms.subagent_max_rounds = 5
            spec = TeammateSpec.from_file(path)
        assert spec.tools == ["bash", "read"]


class TestParseFrontmatter:
    def test_valid(self):
        meta, body = _parse_frontmatter("---\nname: x\n---\nBody text.")
        assert meta["name"] == "x"
        assert body == "Body text."

    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just text.")
        assert meta == {}
        assert body == "Just text."

    def test_invalid_yaml(self):
        meta, body = _parse_frontmatter("---\n: [bad yaml\n---\nBody.")
        assert meta == {}
        assert body == "Body."


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class TestBusSendAndRead:
    def test_send_then_read(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        bus.send("lead", "alice", "Hello", "message")
        msgs = bus.read_inbox("alice")
        assert len(msgs) == 1
        assert msgs[0]["from"] == "lead"
        assert msgs[0]["content"] == "Hello"
        assert msgs[0]["type"] == "message"
        assert "ts" in msgs[0]

    def test_drain_clears_inbox(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        bus.send("lead", "alice", "msg1")
        first = bus.read_inbox("alice")
        second = bus.read_inbox("alice")
        assert len(first) == 1
        assert len(second) == 0

    def test_empty_inbox(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        assert bus.read_inbox("nobody") == []

    def test_invalid_msg_type_rejected(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        result = bus.send("a", "b", "c", "invalid_type")
        assert "[error]" in result
        assert bus.read_inbox("b") == []

    def test_all_valid_msg_types(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        for mt in VALID_MSG_TYPES:
            result = bus.send("lead", "test", f"body-{mt}", mt)
            assert "Sent" in result
        msgs = bus.read_inbox("test")
        assert len(msgs) == len(VALID_MSG_TYPES)

    def test_concurrent_writes(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                for j in range(20):
                    bus.send(f"w{i}", "target", f"msg-{i}-{j}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        msgs = bus.read_inbox("target")
        assert len(msgs) == 100

    def test_multiple_messages_accumulate(self, tmp_path):
        bus = Bus(tmp_path / "inbox")
        bus.send("a", "inbox_owner", "msg1")
        bus.send("b", "inbox_owner", "msg2")
        bus.send("c", "inbox_owner", "msg3")
        msgs = bus.read_inbox("inbox_owner")
        assert len(msgs) == 3
        assert [m["from"] for m in msgs] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# TeamManager — discovery
# ---------------------------------------------------------------------------


class TestTeamManagerDiscovery:
    def test_dual_dir_discovery(self, tmp_path):
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        _write_md(
            builtin, "alpha",
            "---\nname: alpha\ndescription: Alpha bot.\nmodel: m\nmax_turns: 5\n---\nAlpha.",
        )
        _write_md(
            user, "beta",
            "---\nname: beta\ndescription: Beta bot.\n---\nBeta body.",
        )
        with patch("pip_agent.team.settings") as ms:
            ms.model = "m"
            ms.subagent_max_rounds = 10
            mgr = TeamManager(
                builtin, user, MagicMock(), Profiler(),
            )
        result = mgr.status()
        assert "alpha" in result
        assert "beta" in result

    def test_user_wins_on_collision(self, tmp_path):
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        _write_md(
            builtin, "alice",
            "---\nname: alice\ndescription: Builtin alice.\nmodel: old\n---\nOld.",
        )
        _write_md(
            user, "alice",
            "---\nname: alice\ndescription: User alice.\nmodel: new\n---\nNew.",
        )
        with patch("pip_agent.team.settings") as ms:
            ms.model = "m"
            ms.subagent_max_rounds = 10
            mgr = TeamManager(
                builtin, user, MagicMock(), Profiler(),
            )
        assert "User alice" in mgr.status()
        assert "Builtin alice" not in mgr.status()

    def test_missing_dirs_ok(self, tmp_path):
        mgr = TeamManager(
            tmp_path / "no_builtin",
            tmp_path / "no_user",
            MagicMock(),
            Profiler(),
        )
        assert mgr.status() == "No teammates defined."

    def test_malformed_md_skipped(self, tmp_path):
        d = tmp_path / "team"
        d.mkdir()
        (d / "bad.md").write_text("---\n: [invalid\n---\nbody", encoding="utf-8")
        _write_md(d, "good", SAMPLE_MD)
        mgr = TeamManager(
            tmp_path / "empty", d, MagicMock(), Profiler(),
        )
        result = mgr.status()
        assert "good" in result or "alice" in result


# ---------------------------------------------------------------------------
# TeamManager — spawn
# ---------------------------------------------------------------------------


class TestTeamManagerSpawn:
    def test_spawn_success(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            result = mgr.spawn("alice", "Do some work")
        assert "Spawned" in result
        assert "alice" in result

    def test_spawn_writes_prompt_to_inbox(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Build the feature")
        msgs = mgr._bus.read_inbox("alice")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Build the feature"
        assert msgs[0]["from"] == "lead"

    def test_spawn_already_working_rejected(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task 1")
            result = mgr.spawn("alice", "Task 2")
        assert "[error]" in result
        assert "already working" in result

    def test_spawn_unknown_rejected(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        result = mgr.spawn("nobody", "Task")
        assert "[error]" in result

    def test_spawn_shows_working_in_status(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Work")
        assert "[working]" in mgr.status()


class TestTeamManagerRescan:
    def test_spawn_picks_up_files_created_after_init(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        mgr = TeamManager(
            tmp_path / "builtin", user_dir, MagicMock(), Profiler(),
        )
        assert mgr.status() == "No teammates defined."

        _write_md(user_dir, "alice", SAMPLE_MD)
        with patch.object(Teammate, "start"):
            result = mgr.spawn("alice", "Task")
        assert "Spawned" in result

    def test_status_picks_up_files_created_after_init(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        mgr = TeamManager(
            tmp_path / "builtin", user_dir, MagicMock(), Profiler(),
        )
        assert mgr.status() == "No teammates defined."

        _write_md(user_dir, "alice", SAMPLE_MD)
        result = mgr.status()
        assert "alice" in result
        assert "[available]" in result


# ---------------------------------------------------------------------------
# TeamManager — send
# ---------------------------------------------------------------------------


class TestTeamManagerSend:
    def test_send_to_working_succeeds(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Initial task")
            mgr._bus.read_inbox("alice")  # drain spawn prompt
        result = mgr.send("alice", "Follow-up")
        assert "Sent" in result

    def test_send_to_unspawned_queued(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        result = mgr.send("alice", "hello")
        # Sending to an offline (unspawned) teammate is now allowed —
        # the message is queued and will be delivered on next activation.
        assert "offline" in result
        assert "[error]" not in result

    def test_send_to_done_teammate_queued(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task")
        mgr._on_done("alice")
        result = mgr.send("alice", "follow-up")
        # Sending to a finished teammate is now allowed — queued for next activation.
        assert "offline" in result
        assert "[error]" not in result

    def test_broadcast_only_to_working(self, tmp_path):
        user_dir = tmp_path / "user"
        _write_md(user_dir, "alice", SAMPLE_MD)
        _write_md(
            user_dir, "bob",
            "---\nname: bob\ndescription: Bob.\n---\nBob body.",
        )
        with patch("pip_agent.team.settings") as ms:
            ms.model = "m"
            ms.subagent_max_rounds = 10
            mgr = TeamManager(
                tmp_path / "b", user_dir, MagicMock(), Profiler(),
            )
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task A")
            mgr.spawn("bob", "Task B")
        result = mgr.send("all", "hello everyone", "broadcast")
        assert "Broadcast" in result
        assert "2" in result


# ---------------------------------------------------------------------------
# TeamManager — status (two-state: available / working)
# ---------------------------------------------------------------------------


class TestTeamManagerStatus:
    def test_all_available_by_default(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        result = mgr.status()
        assert "[available]" in result
        assert "[working]" not in result

    def test_working_after_spawn(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task")
        result = mgr.status()
        assert "[working]" in result

    def test_available_after_done(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task")
        mgr._on_done("alice")
        result = mgr.status()
        assert "[available]" in result
        assert "[working]" not in result

    def test_respawn_after_done(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task 1")
        mgr._on_done("alice")
        with patch.object(Teammate, "start"):
            result = mgr.spawn("alice", "Task 2")
        assert "Spawned" in result
        assert "[working]" in mgr.status()


# ---------------------------------------------------------------------------
# TeamManager — other
# ---------------------------------------------------------------------------


class TestTeamManagerReadInbox:
    def test_read_inbox_empty(self, tmp_path):
        mgr = TeamManager(
            tmp_path / "b", tmp_path / "u", MagicMock(), Profiler(),
        )
        assert mgr.read_inbox() == []


class TestTeamManagerLifecycle:
    def test_deactivate_all(self, tmp_path):
        mgr = _make_mgr(tmp_path, "alice")
        with patch.object(Teammate, "start"):
            mgr.spawn("alice", "Task")
        mgr.deactivate_all()
        assert "[available]" in mgr.status()


# ---------------------------------------------------------------------------
# Teammate LLM loop
# ---------------------------------------------------------------------------


class TestTeammateLLMLoop:
    def _make_teammate(self, tmp_path, spec=None):
        if spec is None:
            path = _write_md(tmp_path / "team", "alice", SAMPLE_MD)
            spec = TeammateSpec.from_file(path)
        client = MagicMock()
        bus = Bus(tmp_path / "inbox")
        profiler = Profiler()
        return Teammate(
            spec, client, bus, profiler,
            active_names_fn=lambda: ["alice"],
        ), client, bus

    def test_send_tool_dispatches_to_bus(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        client.messages.create.side_effect = [
            _make_response(
                [_tool_use_block("send", {"to": "lead", "content": "done"})],
                stop_reason="tool_use",
            ),
            _make_response([_text_block("ok")]),
        ]
        bus.send("lead", "alice", "Do work")
        t.start()
        time.sleep(0.5)

        lead_msgs = bus.read_inbox("lead")
        assert any(m["content"] == "done" for m in lead_msgs)

    def test_read_inbox_tool(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        bus.send("lead", "alice", "Initial task")
        bus.send("lead", "alice", "Extra info")

        call_count = [0]

        def create_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                bus.send("lead", "alice", "mid-task update")
                return _make_response(
                    [_tool_use_block("read_inbox", {})],
                    stop_reason="tool_use",
                )
            return _make_response([_text_block("done")])

        client.messages.create.side_effect = create_side_effect
        inbox = bus.read_inbox("alice")
        t._process(inbox)

    def test_max_turns_respected(self, tmp_path):
        md = "---\nname: alice\ndescription: d\nmodel: m\nmax_turns: 2\ntools: [read]\n---\nbody"
        path = _write_md(tmp_path / "team", "alice", md)
        spec = TeammateSpec.from_file(path)
        t, client, bus = self._make_teammate(tmp_path, spec=spec)

        client.messages.create.return_value = _make_response(
            [_tool_use_block("read", {"file_path": "x.txt"})],
            stop_reason="tool_use",
        )

        with patch("pip_agent.team.execute_tool", return_value="content"):
            t._process([{"from": "lead", "type": "message", "content": "go"}])

        assert client.messages.create.call_count == 2

    @patch("pip_agent.team.settings")
    def test_deactivate_request_triggers_response(self, mock_settings, tmp_path):
        mock_settings.model = "m"
        mock_settings.max_tokens = 1024
        mock_settings.subagent_max_rounds = 10
        mock_settings.verbose = False

        t, client, bus = self._make_teammate(tmp_path)
        bus.send("lead", "alice", "go", "deactivate_request")
        t.start()
        time.sleep(0.5)

        lead_msgs = bus.read_inbox("lead")
        assert any(m.get("type") == "deactivate_response" for m in lead_msgs)
        assert t.status == "done"

    def test_tool_allowlist_enforced(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        tools = t._build_tools()
        tool_names = {tool["name"] for tool in tools}
        assert "bash" in tool_names
        assert "read" in tool_names
        assert "write" in tool_names
        assert "send" in tool_names
        assert "read_inbox" in tool_names
        assert "task" not in tool_names
        assert "team_spawn" not in tool_names


# ---------------------------------------------------------------------------
# Single-shot behavior
# ---------------------------------------------------------------------------


class TestTeammateSingleShot:
    def _make_teammate(self, tmp_path, spec=None):
        if spec is None:
            path = _write_md(tmp_path / "team", "alice", SAMPLE_MD)
            spec = TeammateSpec.from_file(path)
        client = MagicMock()
        bus = Bus(tmp_path / "inbox")
        profiler = Profiler()
        return Teammate(
            spec, client, bus, profiler,
            active_names_fn=lambda: ["alice"],
        ), client, bus

    def test_thread_ends_after_processing(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        client.messages.create.return_value = _make_response([_text_block("done")])
        bus.send("lead", "alice", "Do work")
        t.start()
        time.sleep(1)
        assert t.status == "done"
        assert client.messages.create.call_count == 1

    def test_no_reprocess_after_thread_ends(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        client.messages.create.return_value = _make_response([_text_block("done")])
        bus.send("lead", "alice", "Do work")
        t.start()
        time.sleep(1)

        bus.send("lead", "alice", "Another task")
        time.sleep(0.5)

        assert client.messages.create.call_count == 1
        unprocessed = bus.read_inbox("alice")
        assert len(unprocessed) == 1
        assert unprocessed[0]["content"] == "Another task"

    def test_shutdown_during_wait_sets_done(self, tmp_path):
        t, client, bus = self._make_teammate(tmp_path)
        t.start()
        time.sleep(0.3)
        t.stop()
        time.sleep(2.5)
        assert t.status == "done"

    def test_done_fn_called_on_finish(self, tmp_path):
        path = _write_md(tmp_path / "team", "alice", SAMPLE_MD)
        spec = TeammateSpec.from_file(path)
        client = MagicMock()
        bus = Bus(tmp_path / "inbox")
        done_fn = MagicMock()
        t = Teammate(
            spec, client, bus, Profiler(),
            done_fn=done_fn,
        )
        client.messages.create.return_value = _make_response([_text_block("ok")])
        bus.send("lead", "alice", "Task")
        t.start()
        time.sleep(1)
        done_fn.assert_called_once_with("alice")


# ---------------------------------------------------------------------------
# Teammate send (bus-only, no wake)
# ---------------------------------------------------------------------------


class TestTeammateSend:
    def test_send_writes_to_bus_only(self, tmp_path):
        path = _write_md(tmp_path / "team", "alice", SAMPLE_MD)
        spec = TeammateSpec.from_file(path)
        bus = Bus(tmp_path / "inbox")
        t = Teammate(
            spec, MagicMock(), bus, Profiler(),
            active_names_fn=lambda: ["alice", "bob"],
        )
        result = t._handle_send({"to": "bob", "content": "hello"})
        assert "Sent" in result
        msgs = bus.read_inbox("bob")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"

    def test_broadcast_writes_to_bus_only(self, tmp_path):
        path = _write_md(tmp_path / "team", "alice", SAMPLE_MD)
        spec = TeammateSpec.from_file(path)
        bus = Bus(tmp_path / "inbox")
        t = Teammate(
            spec, MagicMock(), bus, Profiler(),
            active_names_fn=lambda: ["alice", "bob"],
        )
        result = t._handle_send({"to": "all", "content": "hey", "msg_type": "broadcast"})
        assert "Broadcast" in result
        bob_msgs = bus.read_inbox("bob")
        lead_msgs = bus.read_inbox("lead")
        assert len(bob_msgs) == 1
        assert len(lead_msgs) == 1
