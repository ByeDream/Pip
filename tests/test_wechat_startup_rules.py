"""Contracts for WeChat startup gating and ``--wechat`` argument semantics."""
from __future__ import annotations

from pathlib import Path

from pip_agent.agent_host import (
    _banner_transports,
    _resolve_wechat_login_target,
    _wechat_is_needed,
)
from pip_agent.channels import ChannelManager
from pip_agent.routing import AgentRegistry, Binding, BindingTable


class TestResolveWechatLoginTarget:
    def test_none_means_flag_not_supplied(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        target, error = _resolve_wechat_login_target(None, registry)
        assert target is None
        assert error is None

    def test_empty_string_defaults_to_main_agent(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        target, error = _resolve_wechat_login_target("", registry)
        assert error is None
        assert target == registry.default_agent().id

    def test_unknown_agent_returns_error(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        target, error = _resolve_wechat_login_target("pipboy", registry)
        assert target is None
        assert error is not None
        assert "does not exist" in error


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

    def test_true_when_qr_login_requested_even_without_binding(
        self, tmp_path: Path,
    ) -> None:
        registry = AgentRegistry(tmp_path)
        bindings = BindingTable()
        assert _wechat_is_needed(
            registry, bindings, wechat_qr_wanted=True,
        ) is True

    def test_false_when_only_invalid_bindings_exist(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path)
        bindings = BindingTable()
        # Unknown agent -> invalid.
        bindings.add(Binding(
            agent_id="missing-agent",
            tier=3,
            match_key="account_id",
            match_value="bot-001",
        ))
        # Empty account id -> invalid.
        bindings.add(Binding(
            agent_id=registry.default_agent().id,
            tier=3,
            match_key="account_id",
            match_value="",
        ))
        assert _wechat_is_needed(registry, bindings) is False


class TestMainWechatArgument:
    def test_flag_without_value_passes_empty_string(self, monkeypatch) -> None:
        from pip_agent import __main__ as main_mod

        observed: dict[str, object] = {}

        def _fake_run_host(**kwargs: object) -> None:
            observed.update(kwargs)

        monkeypatch.setattr(main_mod, "_configure_logging", lambda: None)
        monkeypatch.setattr("pip_agent.console_io.force_utf8_console", lambda: None)
        monkeypatch.setattr("pip_agent.agent_host.run_host", _fake_run_host)

        main_mod.main(["--wechat"])
        assert observed.get("wechat_login_for") == ""


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
        # Registered wechat, idle (no poll, no QR) -> banner omits wechat
        mgr.register(wch)
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=False), wechat_poll_threads=0,
        ) == ["cli"]
        # Poll running
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=False), wechat_poll_threads=1,
        ) == ["cli", "wechat"]
        # QR in flight, no poll yet
        assert _banner_transports(
            mgr, _FakeWeChatController(qr=True), wechat_poll_threads=0,
        ) == ["cli", "wechat"]

