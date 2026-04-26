"""Tests for the ``/theme`` slash-command family (list / set / refresh).

These exercise the dispatcher end-to-end (parse → handler → response)
against a real :class:`ThemeManager` rooted at a temporary workspace
themes directory and a real :class:`HostState`. Together they cover:

* ``list`` shows every theme under ``.pip/themes/`` with an active
  marker and surfaces broken themes under a "Skipped" section.
* ``set`` persists the slug to ``host_state.json`` AND fires a
  live-apply call through the TUI app hook when one is attached.
* ``set`` without a TUI falls back to persist-only with a clear hint.
* ``set`` with an unknown slug does not mutate ``host_state.json``.
* ``refresh`` rescans the filesystem and reports added / removed /
  broken themes without changing the active slug.
* ``/theme`` with no manager (line-mode boot) short-circuits cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pip_agent.channels import InboundMessage
from pip_agent.host_commands import CommandContext, dispatch_command
from pip_agent.host_state import HostState
from pip_agent.routing import AgentRegistry, BindingTable
from pip_agent.tui.manager import ThemeManager

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_VALID_PALETTE = {
    "background": "#000000",
    "foreground": "#ffffff",
    "accent": "#00ff00",
    "accent_dim": "#003300",
    "user_input": "#ffffff",
    "agent_text": "#ffffff",
    "thinking": "#888888",
    "tool_call": "#88ddff",
    "log_info": "#ffffff",
    "log_warning": "#ffcc66",
    "log_error": "#ff6666",
    "status_bar": "#222222",
    "status_bar_text": "#ffffff",
}


def _write_theme(
    root: Path,
    *,
    slug: str,
    display_name: str = "",
    version: str = "0.1.0",
    palette: dict[str, str] | None = None,
) -> Path:
    theme_dir = root / slug
    theme_dir.mkdir(parents=True, exist_ok=True)
    palette_block = "\n".join(
        f'{k} = "{v}"' for k, v in (palette or _VALID_PALETTE).items()
    )
    display_value = display_name or slug.title()
    (theme_dir / "theme.toml").write_text(
        "\n".join(
            [
                "[theme]",
                f'name = "{slug}"',
                f'display_name = "{display_value}"',
                f'version = "{version}"',
                'author = "test"',
                'description = "fixture theme"',
                "show_art = true",
                "show_app_log = true",
                "show_status_bar = true",
                "",
                "[palette]",
                palette_block,
                "",
            ]
        ),
        encoding="utf-8",
    )
    (theme_dir / "theme.tcss").write_text(
        "Screen { background: $surface; }\n", encoding="utf-8",
    )
    return theme_dir


class _FakeTuiApp:
    """Minimal ``PipBoyTuiApp`` stand-in for wiring assertions.

    Records every ``call_later(apply_theme, bundle)`` invocation so
    tests can assert a live-apply happened. Raises when told to, so
    the ``/theme set`` error path is reachable without a real Textual
    app running.
    """

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[Any] = []
        self._raises = raises

    def apply_theme(self, bundle: Any) -> None:
        # The host never invokes apply_theme directly — it goes through
        # ``call_later(apply_theme, bundle)``. This method exists so
        # ``ctx.tui_app.apply_theme`` lookup succeeds at the call site.
        self.calls.append(("direct", bundle))

    def call_later(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        if self._raises:
            raise RuntimeError("simulated call_later failure")
        self.calls.append((fn, args, kwargs))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / ".pip" / "themes").mkdir(parents=True)
    return ws


@pytest.fixture
def populated_workspace(workspace: Path) -> Path:
    """Workspace pre-seeded with two valid themes."""
    themes_root = workspace / ".pip" / "themes"
    _write_theme(themes_root, slug="wasteland", display_name="Wasteland Radiation")
    _write_theme(themes_root, slug="vault-amber", display_name="Vault Amber")
    return workspace


def _ctx(
    *,
    workspace: Path,
    text: str,
    theme_manager: ThemeManager | None,
    host_state: HostState | None,
    active_theme_name: str = "wasteland",
    tui_app: Any | None = None,
    set_active_theme: Any | None = None,
) -> CommandContext:
    inbound = InboundMessage(
        text=text, sender_id="cli-user", channel="cli", peer_id="cli-user",
    )
    return CommandContext(
        inbound=inbound,
        registry=AgentRegistry(workspace),
        bindings=BindingTable(),
        bindings_path=workspace / ".pip" / "bindings.json",
        memory_store=None,  # type: ignore[arg-type]
        scheduler=None,
        invalidate_agent=None,
        wechat_controller=None,
        theme_manager=theme_manager,
        host_state=host_state,
        active_theme_name=active_theme_name,
        tui_app=tui_app,
        set_active_theme=set_active_theme,
    )


# ---------------------------------------------------------------------------
# Bare /theme + usage
# ---------------------------------------------------------------------------


class TestThemeUsage:
    def test_bare_theme_returns_usage(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme",
                theme_manager=mgr,
                host_state=state,
            )
        )
        assert result.handled is True
        body = result.response or ""
        assert "/theme list" in body
        assert "/theme set" in body
        assert "/theme refresh" in body
        # No /theme show anymore.
        assert "/theme show" not in body

    def test_unknown_subcommand_is_helpful(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme lst",
                theme_manager=mgr,
                host_state=state,
            )
        )
        assert result.handled is True
        body = result.response or ""
        assert "Unknown /theme subcommand 'lst'" in body
        assert "/theme list" in body

    def test_theme_without_manager_short_circuits(
        self, workspace: Path,
    ) -> None:
        # Line-mode boot path: no TUI, no theme manager.
        result = dispatch_command(
            _ctx(
                workspace=workspace,
                text="/theme list",
                theme_manager=None,
                host_state=None,
                active_theme_name="",
            )
        )
        assert result.handled is True
        body = (result.response or "").lower()
        assert "theme manager is not active" in body


# ---------------------------------------------------------------------------
# /theme list
# ---------------------------------------------------------------------------


class TestThemeList:
    def test_lists_themes_with_active_marker(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme list",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
            )
        )
        body = result.response or ""
        assert "wasteland *" in body
        assert "vault-amber" in body
        # The active marker must be unique to wasteland.
        assert "vault-amber *" not in body
        assert "Wasteland Radiation" in body
        assert "Vault Amber" in body
        # Origin tags are gone in the single-root world.
        assert "[builtin]" not in body
        assert "[local]" not in body

    def test_empty_workspace_prints_hint(
        self, workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=workspace)
        state = HostState(workspace_pip_dir=workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=workspace,
                text="/theme list",
                theme_manager=mgr,
                host_state=state,
            )
        )
        body = result.response or ""
        assert "No themes available" in body
        assert ".pip/themes/" in body

    def test_broken_theme_is_listed_as_skipped(
        self, populated_workspace: Path,
    ) -> None:
        themes_root = populated_workspace / ".pip" / "themes"
        broken_dir = themes_root / "broken"
        broken_dir.mkdir(parents=True)
        (broken_dir / "theme.toml").write_text(
            "this isn't toml = =\n", encoding="utf-8",
        )
        (broken_dir / "theme.tcss").write_text(
            "Screen { background: black; }\n", encoding="utf-8",
        )

        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme list",
                theme_manager=mgr,
                host_state=state,
            )
        )
        body = result.response or ""
        assert "wasteland" in body
        assert "Skipped" in body
        assert "broken" in body


# ---------------------------------------------------------------------------
# /theme show is gone
# ---------------------------------------------------------------------------


def test_theme_show_is_not_a_subcommand_anymore(
    populated_workspace: Path,
) -> None:
    mgr = ThemeManager(workdir=populated_workspace)
    state = HostState(workspace_pip_dir=populated_workspace / ".pip")
    result = dispatch_command(
        _ctx(
            workspace=populated_workspace,
            text="/theme show",
            theme_manager=mgr,
            host_state=state,
        )
    )
    body = result.response or ""
    # show is rejected as unknown; hint points at the replacement verbs.
    assert "Unknown /theme subcommand 'show'" in body


# ---------------------------------------------------------------------------
# /theme set (live apply + persist)
# ---------------------------------------------------------------------------


class TestThemeSet:
    def test_set_persists_and_live_applies(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        fake_app = _FakeTuiApp()
        updates: list[str] = []
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set vault-amber",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
                tui_app=fake_app,
                set_active_theme=updates.append,
            )
        )
        body = result.response or ""
        assert "Applied live" in body
        # Persisted immediately — no restart required.
        blob = json.loads(state.path.read_text(encoding="utf-8"))
        assert blob == {"tui": {"theme": "vault-amber"}}
        # Exactly one live-apply call deposited into the app pump.
        assert len(fake_app.calls) == 1
        fn, args, _kwargs = fake_app.calls[0]
        bundle = args[0]
        assert bundle.manifest.name == "vault-amber"
        # Host's active-theme tracker was updated.
        assert updates == ["vault-amber"]

    def test_set_without_tui_is_persist_only(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        updates: list[str] = []
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set vault-amber",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
                tui_app=None,
                set_active_theme=updates.append,
            )
        )
        body = result.response or ""
        assert "No TUI attached" in body
        assert "next boot" in body
        # Still persisted, so the next boot picks it up.
        blob = json.loads(state.path.read_text(encoding="utf-8"))
        assert blob == {"tui": {"theme": "vault-amber"}}
        # No live apply => host's cached name stays put.
        assert updates == ["vault-amber"]

    def test_set_handles_live_apply_failure_gracefully(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        fake_app = _FakeTuiApp(raises=True)
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set vault-amber",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
                tui_app=fake_app,
            )
        )
        body = result.response or ""
        assert "live apply failed" in body.lower()
        # The persist step ran first, so the slug still landed on disk.
        blob = json.loads(state.path.read_text(encoding="utf-8"))
        assert blob == {"tui": {"theme": "vault-amber"}}

    def test_set_unknown_slug_lists_options_and_does_not_persist(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set nope",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
            )
        )
        body = result.response or ""
        assert "Unknown theme 'nope'" in body
        assert "wasteland" in body
        assert "vault-amber" in body
        assert state.path.exists() is False

    def test_set_without_arg_is_usage(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set",
                theme_manager=mgr,
                host_state=state,
            )
        )
        assert (result.response or "").startswith("Usage: /theme set <name>")

    def test_set_without_host_state_explains_unavailable(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme set wasteland",
                theme_manager=mgr,
                host_state=None,
                active_theme_name="wasteland",
            )
        )
        body = (result.response or "").lower()
        assert "host_state is unavailable" in body


# ---------------------------------------------------------------------------
# /theme refresh
# ---------------------------------------------------------------------------


class TestThemeRefresh:
    def test_refresh_reports_added_themes(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        # Prime the manager's cache with the initial set.
        mgr.discover()
        # Drop a new theme in, then /theme refresh.
        _write_theme(
            populated_workspace / ".pip" / "themes",
            slug="terminal-green",
            display_name="Terminal Green",
        )
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme refresh",
                theme_manager=mgr,
                host_state=state,
            )
        )
        body = result.response or ""
        assert "+1 new" in body
        assert "terminal-green" in body
        assert "Run `/theme set" in body

    def test_refresh_reports_removed_themes(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        mgr.discover()
        # Delete vault-amber on disk.
        themes_root = populated_workspace / ".pip" / "themes"
        import shutil

        shutil.rmtree(themes_root / "vault-amber")

        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme refresh",
                theme_manager=mgr,
                host_state=state,
            )
        )
        body = result.response or ""
        assert "-1 removed" in body
        assert "vault-amber" in body

    def test_refresh_flags_removed_active_theme(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        mgr.discover()
        import shutil

        shutil.rmtree(populated_workspace / ".pip" / "themes" / "wasteland")

        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme refresh",
                theme_manager=mgr,
                host_state=state,
                active_theme_name="wasteland",
            )
        )
        body = result.response or ""
        assert "Active theme 'wasteland' was removed" in body

    def test_refresh_with_no_changes_is_quiet(
        self, populated_workspace: Path,
    ) -> None:
        mgr = ThemeManager(workdir=populated_workspace)
        state = HostState(workspace_pip_dir=populated_workspace / ".pip")
        mgr.discover()
        result = dispatch_command(
            _ctx(
                workspace=populated_workspace,
                text="/theme refresh",
                theme_manager=mgr,
                host_state=state,
            )
        )
        body = result.response or ""
        assert "No changes" in body
