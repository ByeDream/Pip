"""Unit tests for ``_persist_last_inbound_target``.

Locks down the contract of the heartbeat-routing write side:

* User inbounds (cli / wecom / ...) stamp ``state.json`` so the host
  scheduler's read side can route the next heartbeat to wherever the
  user last spoke from.
* Scheduler-injected senders (``__cron__`` / ``__heartbeat__``) MUST
  NOT overwrite the recorded target — otherwise a single background
  tick would reset the user's real channel back to the cli default.
* Group inbounds MUST NOT update the target — heartbeats are 1:1
  proactive nudges; broadcasting "checking in" into a guild is the
  wrong default.
"""

from __future__ import annotations

from pathlib import Path

from pip_agent.agent_host import _persist_last_inbound_target
from pip_agent.channels import InboundMessage
from pip_agent.host_scheduler import (
    _LAST_INBOUND_ACCOUNT_KEY,
    _LAST_INBOUND_CHANNEL_KEY,
    _LAST_INBOUND_PEER_KEY,
)
from pip_agent.memory import MemoryStore


def _make_store(tmp_path: Path) -> MemoryStore:
    agent_dir = tmp_path / ".pip"
    return MemoryStore(
        agent_dir=agent_dir,
        workspace_pip_dir=agent_dir,
        agent_id="pip-boy",
    )


class TestPersistLastInboundTarget:
    def test_user_inbound_writes_all_three_fields(self, tmp_path: Path):
        store = _make_store(tmp_path)
        inbound = InboundMessage(
            text="hi",
            sender_id="user-abc",
            channel="wecom",
            peer_id="user-abc",
            account_id="bot-1",
        )

        wrote = _persist_last_inbound_target(inbound, store, agent_id="pip-boy")

        assert wrote is True
        st = store.load_state()
        assert st[_LAST_INBOUND_CHANNEL_KEY] == "wecom"
        assert st[_LAST_INBOUND_PEER_KEY] == "user-abc"
        assert st[_LAST_INBOUND_ACCOUNT_KEY] == "bot-1"

    def test_cli_inbound_writes(self, tmp_path: Path):
        """A CLI inbound is still a real user signal — record it so the
        heartbeat keeps targeting CLI even if last_inbound_* started empty.
        """
        store = _make_store(tmp_path)
        inbound = InboundMessage(
            text="hi",
            sender_id="cli-user",
            channel="cli",
            peer_id="cli-user",
        )

        assert _persist_last_inbound_target(inbound, store) is True
        st = store.load_state()
        assert st[_LAST_INBOUND_CHANNEL_KEY] == "cli"
        assert st[_LAST_INBOUND_PEER_KEY] == "cli-user"

    def test_heartbeat_sender_does_not_overwrite(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.save_state(
            {
                _LAST_INBOUND_CHANNEL_KEY: "wecom",
                _LAST_INBOUND_PEER_KEY: "user-abc",
                _LAST_INBOUND_ACCOUNT_KEY: "bot-1",
            }
        )
        inbound = InboundMessage(
            text="ping",
            sender_id="__heartbeat__",
            channel="cli",
            peer_id="cli-user",
        )

        assert _persist_last_inbound_target(inbound, store) is False
        st = store.load_state()
        assert st[_LAST_INBOUND_CHANNEL_KEY] == "wecom"
        assert st[_LAST_INBOUND_PEER_KEY] == "user-abc"

    def test_cron_sender_does_not_overwrite(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.save_state(
            {
                _LAST_INBOUND_CHANNEL_KEY: "wecom",
                _LAST_INBOUND_PEER_KEY: "user-abc",
            }
        )
        inbound = InboundMessage(
            text="run job",
            sender_id="__cron__",
            channel="cli",
            peer_id="cli-user",
        )

        assert _persist_last_inbound_target(inbound, store) is False
        assert store.load_state()[_LAST_INBOUND_CHANNEL_KEY] == "wecom"

    def test_group_inbound_does_not_overwrite(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.save_state({_LAST_INBOUND_CHANNEL_KEY: "wecom"})
        inbound = InboundMessage(
            text="@bot hi",
            sender_id="user-abc",
            channel="wecom",
            peer_id="user-abc",
            guild_id="group-xyz",
            is_group=True,
        )

        assert _persist_last_inbound_target(inbound, store) is False
        assert store.load_state()[_LAST_INBOUND_CHANNEL_KEY] == "wecom"

    def test_empty_channel_does_not_overwrite(self, tmp_path: Path):
        """Defence-in-depth: an inbound without a channel string is
        meaningless to route to and would corrupt the recorded target.
        """
        store = _make_store(tmp_path)
        store.save_state({_LAST_INBOUND_CHANNEL_KEY: "wecom"})
        inbound = InboundMessage(
            text="hi",
            sender_id="user-abc",
            channel="",
            peer_id="user-abc",
        )

        assert _persist_last_inbound_target(inbound, store) is False
        assert store.load_state()[_LAST_INBOUND_CHANNEL_KEY] == "wecom"

    def test_user_after_scheduler_overwrites_correctly(self, tmp_path: Path):
        """End-to-end: scheduler tick comes first, then a real user message
        on a different channel — final state must reflect the user, not
        the scheduler default.
        """
        store = _make_store(tmp_path)
        # Tick #1: heartbeat fired with no user history yet — must NOT
        # land cli/cli-user into state, leaving it empty.
        hb = InboundMessage(
            text="ping",
            sender_id="__heartbeat__",
            channel="cli",
            peer_id="cli-user",
        )
        _persist_last_inbound_target(hb, store)
        assert _LAST_INBOUND_CHANNEL_KEY not in store.load_state()

        # Tick #2: user pings via wecom — that's the new target.
        user = InboundMessage(
            text="hi",
            sender_id="user-abc",
            channel="wecom",
            peer_id="user-abc",
            account_id="bot-1",
        )
        assert _persist_last_inbound_target(user, store) is True

        st = store.load_state()
        assert st[_LAST_INBOUND_CHANNEL_KEY] == "wecom"
        assert st[_LAST_INBOUND_PEER_KEY] == "user-abc"
        assert st[_LAST_INBOUND_ACCOUNT_KEY] == "bot-1"
