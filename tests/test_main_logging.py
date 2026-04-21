"""Logging-configuration tripwire for ``pip_agent.__main__``.

Pip-Boy uses stdlib ``logging`` for scheduler ticks, heartbeat sentinel
suppression, session ids, SDK cost/turn summaries, MCP tool calls, and the
reflect pipeline. Python's default root-logger threshold is WARNING, which
means every ``log.info`` / ``log.debug`` emitted by pip_agent is silently
dropped unless ``logging.basicConfig`` runs first.

Previous refactors repeatedly lost that wiring — the host came up, the
scheduler fired heartbeats, the agent replied HEARTBEAT_OK, dispatch
silenced it, and the CLI showed **nothing** because every internal event was
below WARNING. To an operator, the host looked frozen.

These tests lock the contract down:

* ``_configure_logging`` honours ``VERBOSE`` with a two-tier layout:
  root at INFO (or WARNING in quiet mode) and ``pip_agent.*`` at DEBUG
  only under ``VERBOSE=true``. Third parties are NOT pinned — they ride
  the root level, which we want at INFO (not DEBUG) so their internals
  stay readable.
* ``main()`` always calls ``_configure_logging`` *before* handing control
  to ``run_host``. If a future change drops that call site the regression
  test fails loudly.
"""
from __future__ import annotations

import logging

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_root_logger():
    """Snapshot + restore root / ``pip_agent`` logger state per test.

    ``logging.basicConfig`` is a no-op after the first successful call in a
    process. Tests that want to observe fresh configuration therefore need
    to clear existing handlers (and restore them afterwards so pytest's own
    logging isn't broken).

    We also snapshot the ``pip_agent`` logger's level because the
    verbose-path code mutates it explicitly; without a restore, whichever
    test runs last would leak its level into the rest of the suite.
    """
    root = logging.getLogger()
    pip_logger = logging.getLogger("pip_agent")

    saved_root_handlers = root.handlers[:]
    saved_root_level = root.level
    saved_pip_level = pip_logger.level

    root.handlers.clear()
    yield root
    root.handlers[:] = saved_root_handlers
    root.setLevel(saved_root_level)
    pip_logger.setLevel(saved_pip_level)


# ---------------------------------------------------------------------------
# _configure_logging contract
# ---------------------------------------------------------------------------


class TestConfigureLoggingHonoursVerbose:
    """Two-tier layout: root level tracks VERBOSE, pip_agent gets a bump."""

    def test_verbose_true_sets_root_to_info(self, fresh_root_logger, monkeypatch):
        # Root at INFO (not DEBUG) under verbose — third parties ride the
        # root level and we specifically do NOT want their DEBUG firehose.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", True)
        main_mod._configure_logging()

        assert fresh_root_logger.level == logging.INFO

    def test_verbose_true_bumps_pip_agent_to_debug(
        self, fresh_root_logger, monkeypatch,
    ):
        # pip_agent is the ONE logger that should see DEBUG under verbose
        # — that is the whole point of the firehose switch.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", True)
        main_mod._configure_logging()

        assert logging.getLogger("pip_agent").level == logging.DEBUG

    def test_verbose_false_sets_root_to_warning(
        self, fresh_root_logger, monkeypatch,
    ):
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", False)
        main_mod._configure_logging()

        assert fresh_root_logger.level == logging.WARNING

    def test_verbose_false_leaves_pip_agent_unpinned(
        self, fresh_root_logger, monkeypatch,
    ):
        # Quiet mode inherits root — pip_agent.debug/info are hidden and
        # pip_agent.warning still flows through. If a future change pins
        # pip_agent under quiet mode, errors would suddenly shift level
        # and quiet mode would start leaking INFO.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", False)
        main_mod._configure_logging()

        assert logging.getLogger("pip_agent").level == logging.NOTSET


class TestThirdPartyLibsRideRootLevel:
    """Third parties are NOT pinned — they inherit root.

    The user requirement that drove this design: "I don't need DEBUG from
    third-party libs, only Pip needs DEBUG; mcp / httpx / httpcore can all
    be at INFO." Setting root to INFO under verbose, with no per-library
    pin, delivers exactly that: third parties get their INFO+ output (SDK
    startup info, session ids, useful MCP / HTTP traces) but not their
    DEBUG (which is where the scary ``OTEL trace context injection failed``
    traceback and every HTTP-layer packet dump live).
    """

    @pytest.mark.parametrize(
        "third_party",
        ["mcp", "httpx", "httpcore", "claude_agent_sdk", "asyncio", "anyio", "urllib3"],
    )
    def test_third_party_is_not_explicitly_pinned(
        self, fresh_root_logger, monkeypatch, third_party,
    ):
        # ``NOTSET`` (0) = "inherit from ancestor (root)". If someone re-adds
        # an explicit ``setLevel`` for one of these, this fires.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", True)
        main_mod._configure_logging()

        level = logging.getLogger(third_party).level
        assert level == logging.NOTSET, (
            f"{third_party!r} was pinned (level={level}); third parties "
            "should ride the root level so VERBOSE=false silences them and "
            "VERBOSE=true shows their INFO (not DEBUG)"
        )


class TestConfigureLoggingActuallyEmits:
    """End-to-end smoke: configure → emit → verify it lands on stdout.

    The level/handler checks above can all pass while the logger is still
    silent (e.g. if we accidentally stopped passing ``stream=sys.stdout``).
    These tests exercise the wire.
    """

    def test_pip_agent_debug_reaches_stdout_when_verbose(
        self, fresh_root_logger, monkeypatch, capsys,
    ):
        # The whole point of VERBOSE=true: pip_agent DEBUG lands on stdout.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", True)
        main_mod._configure_logging()

        logging.getLogger("pip_agent.host_scheduler").debug("tick debug")
        logging.getLogger("pip_agent.host_scheduler").info("tick info")

        captured = capsys.readouterr()
        assert "tick debug" in captured.out
        assert "tick info" in captured.out

    def test_third_party_debug_suppressed_even_when_verbose(
        self, fresh_root_logger, monkeypatch, capsys,
    ):
        # User requirement: no DEBUG from third parties, ever. Under
        # VERBOSE=true root is INFO, so a httpx DEBUG record is dropped
        # at the root effective-level check.
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", True)
        main_mod._configure_logging()

        logging.getLogger("httpx").debug("packet dump nobody wants")
        logging.getLogger("httpx").info("GET /v1/messages")

        captured = capsys.readouterr()
        assert "packet dump" not in captured.out
        assert "GET /v1/messages" in captured.out

    def test_everything_suppressed_below_warning_when_not_verbose(
        self, fresh_root_logger, monkeypatch, capsys,
    ):
        from pip_agent import __main__ as main_mod

        monkeypatch.setattr(main_mod.settings, "verbose", False)
        main_mod._configure_logging()

        logging.getLogger("pip_agent.host_scheduler").info("should be hidden")
        logging.getLogger("pip_agent.host_scheduler").warning("should show")
        logging.getLogger("httpx").info("also hidden")
        logging.getLogger("httpx").warning("also shown")

        captured = capsys.readouterr()
        assert "should be hidden" not in captured.out
        assert "also hidden" not in captured.out
        assert "should show" in captured.out
        assert "also shown" in captured.out


# ---------------------------------------------------------------------------
# main() regression tripwire
# ---------------------------------------------------------------------------


class TestMainWiresLoggingBeforeRunHost:
    """If this class starts failing, someone removed the
    ``_configure_logging()`` call from ``main()`` (or moved it after
    ``run_host``). Do not ``xfail`` it — fix the call site.
    """

    def test_logging_is_configured_before_run_host_under_verbose(
        self, fresh_root_logger, monkeypatch,
    ):
        from pip_agent import __main__ as main_mod

        observed: dict[str, object] = {}

        def _fake_run_host(**kwargs: object) -> None:
            observed["root_has_handler"] = bool(logging.getLogger().handlers)
            observed["root_level"] = logging.getLogger().level
            observed["pip_agent_level"] = logging.getLogger("pip_agent").level

        # main() does a late import of run_host from pip_agent.agent_host,
        # so patch it at the module attribute, not in main_mod.
        monkeypatch.setattr("pip_agent.agent_host.run_host", _fake_run_host)
        monkeypatch.setattr(main_mod.settings, "verbose", True)

        main_mod.main(["--mode", "cli"])

        assert observed.get("root_has_handler"), (
            "main() invoked run_host without configuring logging — "
            "every log.info/debug from pip_agent will be silently dropped"
        )
        assert observed["root_level"] == logging.INFO, (
            "root logger level did not track settings.verbose; "
            "_configure_logging contract broken"
        )
        assert observed["pip_agent_level"] == logging.DEBUG, (
            "pip_agent logger was not bumped to DEBUG under VERBOSE=true; "
            "our own DEBUG output will be swallowed"
        )

    def test_logging_is_configured_before_run_host_under_quiet(
        self, fresh_root_logger, monkeypatch,
    ):
        # Quiet mode must still install a handler — the difference is the
        # threshold, not presence. Otherwise WARNING/ERROR records from
        # pip_agent would also disappear.
        from pip_agent import __main__ as main_mod

        observed: dict[str, object] = {}

        def _fake_run_host(**kwargs: object) -> None:
            observed["root_has_handler"] = bool(logging.getLogger().handlers)
            observed["root_level"] = logging.getLogger().level

        monkeypatch.setattr("pip_agent.agent_host.run_host", _fake_run_host)
        monkeypatch.setattr(main_mod.settings, "verbose", False)

        main_mod.main(["--mode", "cli"])

        assert observed.get("root_has_handler"), "quiet mode dropped the handler"
        assert observed["root_level"] == logging.WARNING
