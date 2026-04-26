"""Direct loader for package-seeded themes (tests + scaffold source).

The runtime loader is :func:`pip_agent.tui.manager.load_theme_bundle`
which operates on any directory on disk. This module's
:func:`load_builtin_theme` is a thin convenience that resolves a slug
against :data:`pip_agent.tui.themes.BUILTIN_THEMES_DIR` — the seed
directory the scaffold copies from — and loads it via the shared
bundle loader.

Used by:

* Snapshot drivers under ``tests/tui_snapshot_apps/`` that want a
  deterministic theme source independent of any workspace.
* Unit tests that need a valid bundle without scaffolding a full
  ``.pip/themes/`` tree on disk.

Production code paths (``run_host``, ``pip-boy doctor``) never hit
this — they go through :class:`pip_agent.tui.ThemeManager`, which
walks ``<workspace>/.pip/themes/`` exclusively.
"""

from __future__ import annotations

from pip_agent.tui.manager import load_theme_bundle
from pip_agent.tui.theme_api import ThemeBundle
from pip_agent.tui.themes import BUILTIN_THEMES_DIR

__all__ = ["load_builtin_theme"]


def load_builtin_theme(name: str) -> ThemeBundle:
    """Load a package-seeded theme by slug.

    Raises :class:`pip_agent.tui.theme_api.ThemeValidationError` when
    the manifest fails the v1 schema check, :class:`FileNotFoundError`
    when the theme directory is missing, and :class:`OSError` for
    other I/O errors. Since seeds ship inside the wheel, any failure
    here is a developer bug; CI surfaces it via the test suite.
    """
    return load_theme_bundle(BUILTIN_THEMES_DIR / name)
