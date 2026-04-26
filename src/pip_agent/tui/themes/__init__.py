"""Package-seeded theme catalogue.

Each subdirectory is one theme (``theme.toml`` + ``theme.tcss`` +
optional ``art.txt``). These are the *seeds* pip-boy copies into
``<workspace>/.pip/themes/`` on first boot — not the runtime source.

Once seeded, the operator's copy under ``.pip/themes/`` is the source
of truth; edits there win, and deletions there stick (the scaffold
will not re-create a theme the operator deleted).

Runtime lookup goes through :class:`pip_agent.tui.ThemeManager`, which
scans the workspace root exclusively. The one exception is
:func:`pip_agent.tui.loader.load_builtin_theme`, a test/snapshot helper
that reads from this directory directly.
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_THEMES_DIR: Path = Path(__file__).resolve().parent

__all__ = ["BUILTIN_THEMES_DIR"]
