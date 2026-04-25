"""Contracts for WeChat startup gating + banner display.

Boot only auto-registers the WeChat channel when valid tier-3 bindings
already exist; first-time scans go through ``/wechat add <agent_id>``
which lazily bootstraps via :meth:`AgentHost.ensure_wechat_controller`.
There is no longer a ``--wechat`` launch flag.
"""
from __future__ import annotations

from pathlib import Path

from pip_agent.agent_host import _banner_transports, _wechat_is_needed
from pip_agent.channels import ChannelManager
from pip_agent.routing import AgentRegistry, Binding, BindingTable


class TestWechatStartupGate:
    def test_true_when_tier3_binding_points_to_existing_agent(
        self, tmp_path: Path,
    ) -> None:
        registry = AgentRegistry(tmp_path)
        bindings = BindingTable()
        bindings.add(Binding(
            agent_id=registry.default_agent().id,
            tier=3,
            match_key="account_id",
            match_value="bot-001",
        ))
        assert _wechat_is_needed(registry, bindings) is True

    def test_false_when_no_bindings(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        bindings = BindingTable()
        # First-time install: no bindings, no auto-start. Operator
        # bootstraps via ``/wechat add`` from the CLI.
        assert _wechat_is_needed(registry, bindings) is False

    def test_false_when_only_invalid_bindings_exist(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        bindings = BindingTable()
        bindings.add(Binding(
            agent_id="missing-agent",
            tier=3,
            match_key="account_id",
            match_value="bot-001",
        ))
        bindings.add(Binding(
            agent_id=registry.default_agent().id,
            tier=3,
            match_key="account_id",
            match_value="",
        ))
        assert _wechat_is_needed(registry, bindings) is False


class _FakeWeChatController:
    def __init__(self, *, qr: bool) -> None:
        self._qr = qr

    def is_qr_in_progress(self) -> bool:
        return self._qr


class TestBannerTransports:
    def test_includes_wechat_only_when_polling_or_qr(self) -> None:
        mgr = ChannelManager()
        mgr.register(type("C", (), {"name": "cli", "close": lambda self: None})())
        wch = type("W", (), {"name": "wechat", "close": lambda self: None})()
        mgr.register(wch)
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=False), wechat_poll_threads=0,
        ) == ["cli"]
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=False), wechat_poll_threads=1,
        ) == ["cli", "wechat"]
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=True), wechat_poll_threads=0,
        ) == ["cli", "wechat"]
