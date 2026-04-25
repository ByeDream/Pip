"""Cold-start bootstrap hooks in :func:`pip_agent.agent_host.run_host`.

We don't exercise the full ``run_host`` here (it would spin up the
ChannelManager / asyncio loop). Instead we unit-test the small helpers
``run_host`` calls before the registry is built, and assert the
contract-side behaviour: failures degrade to logs, success forwards
the parsed CSV into :func:`pip_agent.plugins.ensure_marketplaces`.

The marketplace bootstrap is the only such helper today; if more land
later (e.g. pre-warming the model registry), they go in this file.
"""
from __future__ import annotations

import logging

import pytest

from pip_agent import agent_host
from pip_agent import plugins as plug


@pytest.fixture
def reset_settings(monkeypatch):
    """Save / restore the bootstrap_marketplaces setting per test.

    Real ``settings`` is a singleton — leaking a value would corrupt
    later tests in the suite.
    """
    saved = agent_host.settings.bootstrap_marketplaces
    yield
    agent_host.settings.bootstrap_marketplaces = saved


class TestBootstrapPluginMarketplaces:

    def test_empty_env_var_skips_entirely(self, monkeypatch, reset_settings):
        agent_host.settings.bootstrap_marketplaces = ""

        called = False

        def fake_ensure(_specs, **_kw):  # pragma: no cover - guard
            nonlocal called
            called = True

        monkeypatch.setattr(plug, "ensure_marketplaces", fake_ensure)
        agent_host._bootstrap_plugin_marketplaces()
        assert called is False

    def test_whitespace_only_env_var_skips_entirely(
        self, monkeypatch, reset_settings,
    ):
        agent_host.settings.bootstrap_marketplaces = "   "

        called = False

        def fake_ensure(_specs, **_kw):  # pragma: no cover - guard
            nonlocal called
            called = True

        monkeypatch.setattr(plug, "ensure_marketplaces", fake_ensure)
        agent_host._bootstrap_plugin_marketplaces()
        assert called is False

    def test_csv_is_split_and_forwarded(self, monkeypatch, reset_settings):
        agent_host.settings.bootstrap_marketplaces = (
            "anthropics/claude-plugins-official, acme/foo "
        )

        observed: list[list[str]] = []

        async def fake_ensure(specs, **_kw):
            observed.append(list(specs))
            return list(specs)

        monkeypatch.setattr(plug, "ensure_marketplaces", fake_ensure)
        agent_host._bootstrap_plugin_marketplaces()
        # Outer ``.strip()`` trims the whole CSV, then ``split(",")`` keeps
        # leading whitespace inside each chunk. ``ensure_marketplaces``
        # itself does a per-spec ``.strip()`` (see
        # ``test_plugins.TestEnsureMarketplaces.test_strips_whitespace_around_specs``)
        # so we don't need to over-clean here.
        assert observed == [
            ["anthropics/claude-plugins-official", " acme/foo"],
        ]

    def test_run_sync_failure_swallowed_with_warning(
        self, monkeypatch, reset_settings, caplog,
    ):
        agent_host.settings.bootstrap_marketplaces = "acme/foo"

        async def boom(_specs, **_kw):
            raise RuntimeError("subprocess vanished")

        monkeypatch.setattr(plug, "ensure_marketplaces", boom)
        with caplog.at_level(logging.WARNING, logger=agent_host.__name__):
            agent_host._bootstrap_plugin_marketplaces()  # no exception
        assert any(
            "marketplace bootstrap aborted" in rec.message
            for rec in caplog.records
        )

    def test_added_summary_logged_at_info(
        self, monkeypatch, reset_settings, caplog,
    ):
        agent_host.settings.bootstrap_marketplaces = "acme/foo,acme/bar"

        async def fake_ensure(specs, **_kw):
            return list(specs)

        monkeypatch.setattr(plug, "ensure_marketplaces", fake_ensure)
        with caplog.at_level(logging.INFO, logger=agent_host.__name__):
            agent_host._bootstrap_plugin_marketplaces()
        # One concise summary line, not one per spec.
        summary = [
            rec for rec in caplog.records
            if "bootstrapped" in rec.message and "marketplace" in rec.message
        ]
        assert len(summary) == 1
        assert "acme/foo" in summary[0].message
        assert "acme/bar" in summary[0].message

    def test_no_added_no_summary_log(
        self, monkeypatch, reset_settings, caplog,
    ):
        agent_host.settings.bootstrap_marketplaces = "acme/foo"

        async def fake_ensure(_specs, **_kw):
            return []

        monkeypatch.setattr(plug, "ensure_marketplaces", fake_ensure)
        with caplog.at_level(logging.INFO, logger=agent_host.__name__):
            agent_host._bootstrap_plugin_marketplaces()
        assert not any(
            "bootstrapped" in rec.message and "marketplace" in rec.message
            for rec in caplog.records
        )
