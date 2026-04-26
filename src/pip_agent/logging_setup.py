"""File-logging setup with per-run rotation.

Pip-Boy's interactive surface captures logs into either the stdout
StreamHandler (line mode) or the Textual ``#app-log`` pane (TUI mode),
and both vanish the moment the process exits. An operator (or, far
more commonly, an LLM that was driving Pip-Boy as a sub-agent) has no
way to go back and inspect what happened during the last run.

This module installs a persistent ``logging.FileHandler`` under
``<workspace>/.pip/log/pip-boy.log`` that captures the same records
the interactive handler sees. On every host boot the previous run's
log is rotated:

    pip-boy.2.log  — two runs ago (oldest kept)
    pip-boy.1.log  — previous run
    pip-boy.log    — current run

Anything older than ``pip-boy.2.log`` is dropped so the directory
doesn't balloon across long-running deployments. Three files is the
sweet spot: "right now", "the run I'm debugging", and "one more back
for comparison" — enough to debug a regression, few enough to never
leak more than a few MB total.

The file handler is installed **alongside** whatever console /TUI
handler the host's other bring-up code attaches; the two do not
interfere. In particular, ``_bootstrap_tui`` removes the stdout
``StreamHandler`` when the TUI wins the capability ladder — that
filter is keyed on ``stream is sys.stdout``, which the FileHandler's
stream never matches, so the file log survives the swap.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = [
    "LOG_DIR_NAME",
    "LOG_FILENAME",
    "LOG_KEEP_BACKUPS",
    "install_file_logging",
    "rotate_logs",
]

LOG_DIR_NAME: str = "log"
"""Subdirectory of ``.pip/`` where run logs live."""

LOG_FILENAME: str = "pip-boy.log"
"""Filename of the *current* run's log. Rotated backups append
``.1`` / ``.2`` before the ``.log`` suffix."""

LOG_KEEP_BACKUPS: int = 2
"""How many rotated backups to retain.

Two backups + the current log = three files total. Tightening this to
``1`` loses A/B comparisons between consecutive runs; loosening it
grows the directory without much debugging payoff."""

_LOG_FORMAT: str = "%(asctime)s %(levelname)s %(name)s %(message)s"
"""Matches the console formatter installed by ``__main__._configure_logging``
so a file dump reads exactly like the interactive output — no separate
grep vocabulary."""


def _backup_name(index: int) -> str:
    """``.1.log`` / ``.2.log`` / ... rotated filename for ``index``.

    Index 0 is the live log (``pip-boy.log``); positive indices are
    progressively older backups. We construct the name here instead
    of inlining so tests + callers share the exact string format.
    """
    if index <= 0:
        return LOG_FILENAME
    stem = LOG_FILENAME.rsplit(".", 1)[0]
    return f"{stem}.{index}.log"


def rotate_logs(log_dir: Path) -> None:
    """Shift existing log files one slot older and discard overflow.

    Called exactly once per boot, before :func:`install_file_logging`
    opens the fresh ``pip-boy.log``. The walk runs oldest → newest so
    each rename targets a slot that's already been freed by the
    previous step; this removes the need for a temp file and keeps
    the operation atomic enough that a crash mid-rotation leaves
    behind at most one extra file (not a corrupted one).

    Missing files are fine: a fresh workspace has no logs yet and the
    function is still expected to succeed.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    oldest = log_dir / _backup_name(LOG_KEEP_BACKUPS)
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError:
            # Another process (or a Windows AV scanner) has the file
            # open. The rename chain below will simply fail into the
            # same path; we let that error propagate so the caller
            # sees a real problem instead of silently overwriting.
            pass

    for i in range(LOG_KEEP_BACKUPS - 1, -1, -1):
        src = log_dir / _backup_name(i)
        dst = log_dir / _backup_name(i + 1)
        if src.exists():
            src.replace(dst)


def install_file_logging(workdir: Path) -> logging.FileHandler:
    """Rotate the previous run's log and attach a fresh ``FileHandler``.

    The returned handler is added to the root logger so every module
    inherits it automatically. Its level is left at ``NOTSET`` — the
    root logger's threshold (set by
    :func:`pip_agent.__main__._configure_logging` from the ``VERBOSE``
    flag) decides what reaches the handler, so the file log content
    matches the console 1:1.

    Callers should keep the returned handle around if they want to
    ``flush()`` it (the host does on shutdown) or remove it at exit.
    """
    log_dir = workdir / ".pip" / LOG_DIR_NAME
    rotate_logs(log_dir)

    log_path = log_dir / LOG_FILENAME
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler
