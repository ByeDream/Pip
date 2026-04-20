"""Tests for ``agent_host._format_prompt`` — user / cron / heartbeat wrapping."""

from __future__ import annotations

from pip_agent.agent_host import _format_prompt
from pip_agent.channels import InboundMessage


class TestFormatPrompt:
    def test_cli_user_passes_through(self):
        inbound = InboundMessage(
            text="hello", sender_id="cli-user", channel="cli", peer_id="cli-user",
        )
        assert _format_prompt(inbound, None) == "hello"

    def test_cron_sentinel_wraps_regardless_of_channel(self):
        inbound = InboundMessage(
            text="summarize news", sender_id="__cron__",
            channel="cli", peer_id="cli-user", agent_id="pip-boy",
        )
        out = _format_prompt(inbound, None)
        assert out.startswith("<cron_task>")
        assert out.endswith("</cron_task>")
        assert "summarize news" in out

    def test_heartbeat_sentinel_wraps_regardless_of_channel(self):
        inbound = InboundMessage(
            text="still alive", sender_id="__heartbeat__",
            channel="cli", peer_id="cli-user", agent_id="pip-boy",
        )
        out = _format_prompt(inbound, None)
        assert out.startswith("<heartbeat>")
        assert out.endswith("</heartbeat>")
        assert "still alive" in out

    def test_remote_channel_wraps_user_query(self):
        inbound = InboundMessage(
            text="hi bot", sender_id="u123", channel="wechat",
            peer_id="u123", is_group=False,
        )
        out = _format_prompt(inbound, None)
        assert "<user_query" in out
        assert 'from="wechat:u123"' in out
        assert 'status="unverified"' in out

    def test_remote_group_includes_group_attr(self):
        inbound = InboundMessage(
            text="hi bot", sender_id="u123", channel="wecom",
            peer_id="g1", guild_id="g1", is_group=True,
        )
        out = _format_prompt(inbound, None)
        assert 'group="true"' in out

    def test_leading_at_mention_stripped(self):
        inbound = InboundMessage(
            text="@Pip hey", sender_id="u1", channel="wechat",
            peer_id="p1",
        )
        out = _format_prompt(inbound, None)
        assert "@Pip" not in out
        assert "hey" in out
