"""Tests for the unified slash-command dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from pip_agent.channels import InboundMessage
from pip_agent.commands import CommandContext, CommandResult, dispatch_command
from pip_agent.routing import (
    AgentConfig,
    AgentRegistry,
    Binding,
    BindingTable,
)


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "pip-boy.md").write_text(
        "---\nname: Pip-Boy\nmodel: claude-sonnet-4-6\ndm_scope: per-guild\n---\nBody.\n",
        encoding="utf-8",
    )
    (d / "pm-bot.md").write_text(
        "---\nname: PM Bot\nmodel: gpt-4\ndm_scope: per-guild\n---\nPM stuff.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def registry(agents_dir):
    return AgentRegistry(agents_dir)


@pytest.fixture
def bindings_path(tmp_path):
    return tmp_path / "bindings.json"


def _make_ctx(
    text: str,
    registry: AgentRegistry,
    bindings_path: Path,
    *,
    channel: str = "wecom",
    peer_id: str = "u1",
    guild_id: str = "",
    is_group: bool = False,
) -> CommandContext:
    bt = BindingTable()
    bt.load(bindings_path)
    return CommandContext(
        inbound=InboundMessage(
            text=text,
            sender_id=peer_id,
            channel=channel,
            peer_id=peer_id,
            guild_id=guild_id,
            is_group=is_group,
        ),
        registry=registry,
        bindings=bt,
        bindings_path=bindings_path,
        workdir="/tmp/test",
    )


# ---------------------------------------------------------------------------
# dispatch_command basics
# ---------------------------------------------------------------------------

class TestDispatchCommand:
    def test_non_command(self, registry, bindings_path):
        ctx = _make_ctx("hello world", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is False

    def test_unknown_command(self, registry, bindings_path):
        ctx = _make_ctx("/unknown", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is False

    def test_at_mention_stripped(self, registry, bindings_path):
        ctx = _make_ctx("@Pip-Boy /status", registry, bindings_path, channel="wecom")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "pip-boy" in result.response.lower()

    def test_at_mention_bare_at_stripped(self, registry, bindings_path):
        """WeCom SDK sometimes strips the name, leaving just '@ /cmd'."""
        ctx = _make_ctx("@ /status", registry, bindings_path, channel="wecom")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "pip-boy" in result.response.lower()

    def test_double_at_mention_stripped(self, registry, bindings_path):
        """Double @-mention: '@ @Pip-Boy /cmd' should also be parsed."""
        ctx = _make_ctx("@ @Pip-Boy /status", registry, bindings_path, channel="wecom")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "pip-boy" in result.response.lower()

    def test_at_mention_non_command(self, registry, bindings_path):
        ctx = _make_ctx("@Pip-Boy hello", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is False


# ---------------------------------------------------------------------------
# /init
# ---------------------------------------------------------------------------

class TestInit:
    def test_help(self, registry, bindings_path):
        ctx = _make_ctx("/init --help", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "Usage" in result.response

    def test_no_args(self, registry, bindings_path):
        ctx = _make_ctx("/init", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "Usage" in result.response

    def test_bind_guild(self, registry, bindings_path):
        ctx = _make_ctx(
            "/init pm-bot", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "PM Bot" in result.response

        bt = BindingTable()
        bt.load(bindings_path)
        aid, _ = bt.resolve(guild_id="g1")
        assert aid == "pm-bot"

    def test_bind_peer(self, registry, bindings_path):
        ctx = _make_ctx("/init pip-boy", registry, bindings_path, peer_id="u2")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "Pip-Boy" in result.response

        bt = BindingTable()
        bt.load(bindings_path)
        aid, _ = bt.resolve(peer_id="u2")
        assert aid == "pip-boy"

    def test_auto_create_agent(self, registry, bindings_path):
        ctx = _make_ctx(
            "/init new-bot", registry, bindings_path,
            guild_id="g3", is_group=True,
        )
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "Created new agent" in result.response
        assert "new-bot" in result.response

        assert registry.get_agent("new-bot") is not None
        md_path = registry.agents_dir / "new-bot.md"
        assert md_path.exists()

        bt = BindingTable()
        bt.load(bindings_path)
        aid, _ = bt.resolve(guild_id="g3")
        assert aid == "new-bot"

    def test_with_overrides(self, registry, bindings_path):
        ctx = _make_ctx(
            "/init pm-bot --model gpt-4o --scope main --max-tokens 2048",
            registry, bindings_path,
            guild_id="g2", is_group=True,
        )
        result = dispatch_command(ctx)
        assert result.handled is True

        bt = BindingTable()
        bt.load(bindings_path)
        aid, binding = bt.resolve(guild_id="g2")
        assert aid == "pm-bot"
        assert binding.overrides["model"] == "gpt-4o"
        assert binding.overrides["scope"] == "main"
        assert binding.overrides["max_tokens"] == "2048"

    def test_unknown_flag(self, registry, bindings_path):
        ctx = _make_ctx("/init pm-bot --bogus", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "Unknown" in result.response

    def test_replaces_existing_binding(self, registry, bindings_path):
        ctx1 = _make_ctx(
            "/init pip-boy", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        dispatch_command(ctx1)

        ctx2 = _make_ctx(
            "/init pm-bot", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        ctx2.bindings.load(bindings_path)
        dispatch_command(ctx2)

        bt = BindingTable()
        bt.load(bindings_path)
        aid, _ = bt.resolve(guild_id="g1")
        assert aid == "pm-bot"


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_binding(self, registry, bindings_path):
        ctx = _make_ctx(
            "/init pm-bot", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        dispatch_command(ctx)

        ctx2 = _make_ctx(
            "/clear binding", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        ctx2.bindings.load(bindings_path)
        result = dispatch_command(ctx2)
        assert result.handled is True
        assert "removed" in result.response.lower()

    def test_clear_no_binding(self, registry, bindings_path):
        ctx = _make_ctx("/clear", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "no binding" in result.response.lower()

    def test_clear_history_stub(self, registry, bindings_path):
        ctx = _make_ctx("/clear history", registry, bindings_path)
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "not yet" in result.response.lower()


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_default_status(self, registry, bindings_path):
        ctx = _make_ctx("/status", registry, bindings_path, channel="cli", peer_id="cli-user")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert "pip-boy" in result.response.lower()

    def test_status_with_binding(self, registry, bindings_path):
        ctx = _make_ctx(
            "/init pm-bot", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        dispatch_command(ctx)

        ctx2 = _make_ctx(
            "/status", registry, bindings_path,
            guild_id="g1", is_group=True,
        )
        ctx2.bindings.load(bindings_path)
        result = dispatch_command(ctx2)
        assert result.handled is True
        assert "pm-bot" in result.response.lower()


# ---------------------------------------------------------------------------
# /exit
# ---------------------------------------------------------------------------

class TestExit:
    def test_exit_cli(self, registry, bindings_path):
        ctx = _make_ctx("/exit", registry, bindings_path, channel="cli")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert result.exit_requested is True

    def test_exit_non_cli(self, registry, bindings_path):
        ctx = _make_ctx("/exit", registry, bindings_path, channel="wecom")
        result = dispatch_command(ctx)
        assert result.handled is True
        assert result.exit_requested is False
        assert "CLI" in result.response
