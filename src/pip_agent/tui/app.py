"""``PipBoyTuiApp`` — the locked TUI widget topology.

Layout (LOCKED — themes can hide widgets via the manifest's
``show_*`` flags but cannot rearrange them):

::

    Screen
    ├── #status-bar       1 row, dock top
    └── #main             horizontal flex
        ├── #agent-pane   3fr — model dialog + input
        │   ├── #agent-log
        │   └── #input
        └── #side-pane    1fr — art + app log
            ├── #pipboy-art
            └── #app-log

Three message handlers, one per sink, fed by
:class:`pip_agent.tui.pump.UiPump`. The App never reads ``sys.stdin``
or writes to ``sys.stdout`` directly; everything goes through
widgets.
"""

from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable

from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.widgets import Input, RichLog, Static

from pip_agent.tui.messages import AgentMessage, LogMessage, StatusMessage
from pip_agent.tui.pump import UiPump
from pip_agent.tui.sinks import AgentEvent
from pip_agent.tui.textual_theme import textual_theme_from_bundle
from pip_agent.tui.theme_api import ThemeBundle

__all__ = ["PipBoyTuiApp"]


# Type alias for the host hook: the app forwards every submitted line
# (and only that — no /exit short-circuit; design.md §6) to this
# callable, which the host wires to its inbound queue.
UserLineHandler = Callable[[str], Awaitable[None] | None]


def _rich_log_strip_tail(log_widget: RichLog, n: int) -> None:
    """Remove the last ``n`` rendered strips and fix RichLog geometry.

    Used to replace the streaming assistant tail in-place so the reply
    stays inside ``#agent-log`` instead of a separate widget below it.

    Couples to Textual's ``RichLog`` internals (``lines``, ``_line_cache``,
    ``_widest_line_width``) — revisit if Textual refactors the widget.
    """
    if n <= 0:
        return
    lines = log_widget.lines
    take = min(n, len(lines))
    if take:
        del lines[-take:]
    log_widget._line_cache.clear()
    if not lines:
        log_widget._widest_line_width = 0
    else:
        log_widget._widest_line_width = max(s.cell_length for s in lines)
    log_widget.virtual_size = Size(log_widget._widest_line_width, len(lines))
    log_widget.refresh()


class PipBoyTuiApp(App[None]):
    """Top-level Textual App for Pip-Boy.

    The constructor takes a *theme bundle* (loaded by Phase A's
    :func:`pip_agent.tui.loader.load_builtin_theme`; Phase B will
    swap in :class:`pip_agent.tui.theme_api.ThemeManager`), a
    :class:`UiPump` (the producer-side fan-in), and a callable for
    forwarding user input lines back to the host's inbound queue.

    The App never imports anything from ``pip_agent.agent_host``;
    that's deliberate — the App is a pure view/control surface, not
    a host integration point. Phase A.3 wires the host to the App
    via constructor injection.
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear log"),
    ]

    def __init__(
        self,
        *,
        theme: ThemeBundle,
        pump: UiPump,
        on_user_line: UserLineHandler | None = None,
    ) -> None:
        # The TCSS file isn't reachable from the package data path at
        # ``CSS_PATH`` time (Textual reads it before mount); we attach
        # it via ``CSS`` (raw stylesheet text) instead, which is the
        # supported route for stylesheets bundled inside non-package
        # data. ``stylesheet`` is the runtime equivalent.
        super().__init__()
        self._theme = theme
        self._pump = pump
        self._on_user_line = on_user_line

        # Map ``theme.toml`` palette onto Textual's design tokens. Without
        # this, ``$accent`` / ``$surface`` resolve from ``textual-dark``
        # (orange accent) while the status line still prints the
        # manifest display name — a misleading split.
        _tt = textual_theme_from_bundle(theme)
        self.register_theme(_tt)
        self.theme = _tt.name

        # ``text_delta`` is often chunked per character. Buffer here and
        # rewrite the *tail* of ``#agent-log`` on each chunk so the reply
        # stays one growing block (no per-char rows; no extra pane jump).
        self._stream_buf: str = ""
        self._stream_tail_strips: int = 0
        self._streaming_open = False

    # ------------------------------------------------------------------
    # Stylesheets
    # ------------------------------------------------------------------

    @property
    def CSS(self) -> str:  # type: ignore[override]
        """The active theme's TCSS + a status-bar palette tail.

        Textual reads ``self.CSS`` once during ``App.__init__`` and
        captures it into ``self.stylesheet`` under the key
        ``(inspect.getfile(self.__class__), "PipBoyTuiApp.CSS")``.
        That's a one-shot read — refreshing the stylesheet later does
        *not* re-invoke this property. :meth:`apply_theme` handles
        the live-swap path by writing directly to
        ``self.stylesheet.add_source`` with the same key.
        """
        return self._compose_css(self._theme)

    @staticmethod
    def _compose_css(bundle: ThemeBundle) -> str:
        """Concatenate a bundle's TCSS with a status-bar palette tail.

        The tail hard-codes ``#status-bar`` colours from the manifest
        so the bar doesn't inherit generic ``$boost`` / ``$text`` shades
        from the Textual theme — those variables track the general
        palette but the status bar has dedicated tokens.
        """
        p = bundle.manifest.palette
        tail = (
            "\n/* Manifest palette: status bar */\n"
            f"#status-bar {{\n"
            f"    background: {p.status_bar};\n"
            f"    color: {p.status_bar_text};\n"
            f"}}\n"
        )
        return bundle.tcss + tail

    def _css_source_key(self) -> tuple[str, str]:
        """Key Textual uses to index ``self.CSS`` inside the stylesheet.

        Mirrors the tuple Textual builds when it first ingests the
        property (``app.py`` around line 3375). We need the exact same
        key so :meth:`apply_theme` can overwrite — not duplicate — the
        stylesheet entry when swapping themes at runtime.
        """
        try:
            app_path = inspect.getfile(self.__class__)
        except (TypeError, OSError):
            app_path = ""
        return (app_path, f"{self.__class__.__name__}.CSS")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the locked widget tree.

        ``show_*`` toggles from the manifest hide widgets but never
        change their IDs or relative ordering — snapshot baselines
        rely on the topology being identical across themes.
        """
        manifest = self._theme.manifest

        if manifest.show_status_bar:
            yield Static(self._status_default_text(), id="status-bar")

        with Horizontal(id="main"):
            with Vertical(id="agent-pane"):
                yield RichLog(
                    id="agent-log",
                    highlight=False,
                    markup=True,
                    wrap=True,
                    min_width=0,
                    auto_scroll=True,
                )
                yield Input(
                    placeholder="Type and press Enter — /exit to quit",
                    id="input",
                )
            if manifest.show_app_log or self._theme.art:
                with Vertical(id="side-pane"):
                    if manifest.show_art and self._theme.art:
                        yield Static(self._theme.art, id="pipboy-art")
                    if manifest.show_app_log:
                        yield RichLog(
                            id="app-log",
                            highlight=False,
                            markup=False,
                            wrap=True,
                            min_width=0,
                            auto_scroll=True,
                        )

    def on_mount(self) -> None:
        """Attach the pump after every widget is mounted.

        Order matters: ``attach`` flushes the buffered banner /
        scaffold events into the App's message queue, and those
        handlers need ``query_one`` to resolve. Calling ``attach``
        before mount would race the very events it's supposed to
        deliver.
        """
        self._pump.attach(self)
        try:
            self.query_one("#input", Input).focus()
        except Exception:  # pragma: no cover — input always present in v1
            pass

    # ------------------------------------------------------------------
    # Pump message handlers
    # ------------------------------------------------------------------

    def on_agent_message(self, message: AgentMessage) -> None:
        """Render one agent-pane event."""
        try:
            log_widget = self.query_one("#agent-log", RichLog)
        except Exception:
            return
        event = message.event
        if event.kind == "user_input":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            log_widget.write(Text(f"> {event.text}", style="bold"))
        elif event.kind == "thinking_delta":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            log_widget.write(
                Text(event.text.rstrip("\n"), style="dim italic")
            )
        elif event.kind == "text_delta":
            self._streaming_open = True
            self._stream_buf += event.text
            self._rewrite_stream_tail(log_widget)
        elif event.kind == "plain":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            log_widget.write(Text(event.text or ""), expand=True)
        elif event.kind == "tool_use":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            args = f" {event.text}" if event.text else ""
            log_widget.write(
                Text(f"[tool: {event.name}{args}]", style="cyan")
            )
        elif event.kind == "markdown":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            log_widget.write(
                Markdown(event.text or "", justify="left"),
            )
        elif event.kind == "finalize":
            # Streaming used ``Text`` for stable tail rewrites; swap the
            # finished buffer for ``Markdown`` once so ** / ` / lists
            # render instead of showing raw control characters.
            self._flush_stream_buffer(log_widget, materialize_markdown=True)
            footer = self._format_footer(event)
            self._streaming_open = False
            log_widget.write(Text(footer, style="dim"))
        elif event.kind == "error":
            self._flush_stream_buffer(log_widget)
            self._streaming_open = False
            log_widget.write(Text(f"[error] {event.text}", style="bold red"))

    def on_log_message(self, message: LogMessage) -> None:
        """Render one stdlib log record into the app-log pane."""
        try:
            log_widget = self.query_one("#app-log", RichLog)
        except Exception:
            return
        record = message.record
        line = self._format_log_record(record)
        if record.levelno >= logging.ERROR:
            log_widget.write(Text(line, style="bold red"))
        elif record.levelno >= logging.WARNING:
            log_widget.write(Text(line, style="yellow"))
        else:
            log_widget.write(Text(line, style="dim"))

    def on_status_message(self, message: StatusMessage) -> None:
        """Render one status-bar update."""
        event = message.event
        if event.kind in {"banner", "ready", "channel_ready", "scheduler"}:
            text = event.text
        elif event.kind == "channel_lost":
            text = f"[!] {event.text}"
        elif event.kind == "shutdown":
            text = f"powering down — {event.text}".rstrip(" —")
        else:  # pragma: no cover — kind enum-checked at construction
            text = event.text
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.update(text)
        except Exception:
            try:
                log_fallback = self.query_one("#agent-log", RichLog)
                self._flush_stream_buffer(log_fallback)
                log_fallback.write(Text(text, style="bold green"))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Forward each submitted line to the host's inbound queue.

        The TUI does NOT short-circuit ``/exit`` — design.md §6
        explicitly forbids that. ``/exit`` flows through the host
        like any other command so ``flush_and_rotate`` runs and
        observations are not lost. The host calls :meth:`request_exit`
        once teardown completes; only then does the App actually
        terminate.
        """
        text = event.value
        try:
            self.query_one("#input", Input).clear()
        except Exception:
            pass
        if not text.strip():
            return
        if self._on_user_line is not None:
            try:
                result = self._on_user_line(text)
                if result is not None and hasattr(result, "__await__"):
                    self.run_worker(result, exclusive=False)
            except Exception:
                logging.getLogger(__name__).exception(
                    "on_user_line handler raised; suppressing to keep "
                    "TUI responsive."
                )

    # ------------------------------------------------------------------
    # External shutdown signal
    # ------------------------------------------------------------------

    def request_exit(self) -> None:
        """Tell the App to exit. Called by the host after teardown.

        This is the *only* path the host should use to stop the TUI —
        calling :meth:`App.exit` directly from a sink or worker thread
        bypasses the pump's thread-safety contract.
        """
        self.call_later(self.exit)

    # ------------------------------------------------------------------
    # Runtime theme swap
    # ------------------------------------------------------------------

    def apply_theme(self, bundle: ThemeBundle) -> None:
        """Swap the active theme without restarting the host.

        Wired to ``/theme set`` via ``call_later(apply_theme, bundle)``
        so the mutation runs on Textual's own message pump (the
        slash-command handler lives on the host's asyncio task and
        cannot poke widget state directly).

        The agent log, app log, and input widget keep their state —
        only colours, TCSS, ASCII art, and status-bar display_name
        change. The widget topology is LOCKED, so ``show_*`` toggles
        flip ``.display`` instead of re-composing the tree; any theme
        that was rendering with a side pane can therefore hide it,
        and vice versa, without breaking the snapshot contract.
        """
        if (
            bundle.manifest.name == self._theme.manifest.name
            and bundle.path == self._theme.path
        ):
            return

        self._theme = bundle

        new_textual_theme = textual_theme_from_bundle(bundle)
        self.register_theme(new_textual_theme)

        key = self._css_source_key()
        self.stylesheet.add_source(
            self._compose_css(bundle),
            read_from=key,
            is_default_css=False,
        )

        self.theme = new_textual_theme.name

        self._apply_visibility(bundle)
        self._apply_art(bundle)
        self._apply_status_bar_text(bundle)

        self.refresh(layout=True)

    def _apply_visibility(self, bundle: ThemeBundle) -> None:
        """Honour ``show_app_log`` / ``show_status_bar`` at runtime.

        Widgets were composed once at mount; we toggle ``display``
        rather than unmounting so a later theme with the pane enabled
        lights up again without a re-mount. Missing widgets (e.g. a
        theme that started with ``show_app_log=False`` never rendered
        ``#app-log``) are simply skipped — Textual raises
        :class:`NoMatches` and we catch it.
        """
        m = bundle.manifest
        for widget_id, visible in (
            ("#status-bar", m.show_status_bar),
            ("#app-log", m.show_app_log),
        ):
            try:
                widget = self.query_one(widget_id)
            except Exception:
                continue
            widget.display = visible

        try:
            side_pane = self.query_one("#side-pane")
        except Exception:
            side_pane = None
        if side_pane is not None:
            side_pane.display = bool(m.show_app_log or (m.show_art and bundle.art))

    def _apply_art(self, bundle: ThemeBundle) -> None:
        """Refresh ``#pipboy-art`` content + visibility for the new bundle."""
        try:
            art_widget = self.query_one("#pipboy-art", Static)
        except Exception:
            return
        m = bundle.manifest
        if m.show_art and bundle.art:
            art_widget.update(bundle.art)
            art_widget.display = True
        else:
            art_widget.display = False

    def _apply_status_bar_text(self, bundle: ThemeBundle) -> None:
        """Reset the status bar's default text to the new theme's name.

        Overwritten the moment the next ``StatusMessage`` arrives; the
        reset matters for the idle gap between the theme swap and the
        next status event, when stale text would otherwise show.
        """
        try:
            status_bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        status_bar.update(self._status_default_text())

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_clear_log(self) -> None:
        """``Ctrl+L`` clears the agent log only — app log is preserved."""
        self._stream_buf = ""
        self._stream_tail_strips = 0
        try:
            self.query_one("#agent-log", RichLog).clear()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _status_default_text(self) -> str:
        m = self._theme.manifest
        return f"Pip-Boy — theme: {m.display_name} v{m.version}"

    def _flush_stream_buffer(
        self, log_widget: RichLog, *, materialize_markdown: bool = False,
    ) -> None:
        """Drop streaming bookkeeping; optionally re-render the tail as Markdown."""
        buf = self._stream_buf
        n = self._stream_tail_strips
        if materialize_markdown and buf.strip() and n > 0:
            _rich_log_strip_tail(log_widget, n)
            log_widget.write(Markdown(buf, justify="left"))
        self._stream_buf = ""
        self._stream_tail_strips = 0

    def _rewrite_stream_tail(self, log_widget: RichLog) -> None:
        buf = self._stream_buf
        if not buf:
            self._stream_tail_strips = 0
            return
        _rich_log_strip_tail(log_widget, self._stream_tail_strips)
        before = len(log_widget.lines)
        log_widget.write(Text(buf), expand=True)
        self._stream_tail_strips = len(log_widget.lines) - before

    def _format_footer(self, event: AgentEvent) -> str:
        template = self._theme.manifest.footer_template
        cost_str = f"{event.cost_usd:.4f}" if event.cost_usd else "0.0000"
        usage = event.usage or {}
        try:
            return template.format(
                turns=event.num_turns,
                cost=cost_str,
                elapsed_s=f"{event.elapsed_s:.1f}",
                tokens_in=usage.get("input_tokens", 0),
                tokens_out=usage.get("output_tokens", 0),
                tools=usage.get("tool_calls", 0),
            )
        except (KeyError, IndexError):
            return f"[turns={event.num_turns} cost=${cost_str}]"

    @staticmethod
    def _format_log_record(record: logging.LogRecord) -> str:
        try:
            msg = record.getMessage()
        except Exception:
            msg = record.msg if isinstance(record.msg, str) else repr(record.msg)
        return f"{record.levelname:<7} {record.name}: {msg}"
