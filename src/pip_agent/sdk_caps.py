"""Per-process cache of SDK capabilities reported via ``SystemMessage(init)``.

The Claude Agent SDK announces the slash commands its current session
will dispatch in the ``init`` message at the start of every query
(``message.subtype == "init"``, ``message.data["slash_commands"]``).
Two callers need that list:

1. :mod:`pip_agent.host_commands` — gates ``/T <slash>`` so unknown
   slashes (typos, CLI-only commands like ``/login``) are caught at
   the host without paying a subprocess round-trip.
2. ``/help`` — surfaces what is dispatchable in the current SDK
   install instead of leaving the user to guess.

Scope
-----
The cache is **process-global** because the SDK install is fixed for
the host's lifetime; per-agent / per-session inits should report the
same list. The first non-empty observation wins; subsequent inits do
not overwrite (avoids a partial / stripped list from a degenerate
session clobbering the canonical one). Concurrent ``record`` calls
are guarded by a single lock — contention here is negligible since
init messages arrive at most a few times per second.

Names are stored **lowercased without leading slash** so lookups can
treat them as case-insensitive identifiers; rendering for help text
re-prepends the slash. Empty / non-string entries from the SDK side
are ignored defensively rather than raised — a malformed init must
not break the host's reply path.
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

log = logging.getLogger(__name__)

_lock = threading.Lock()
_slashes: set[str] | None = None


def record(slash_commands: Iterable[object] | None) -> None:
    """Capture the SDK slash list from ``SystemMessage.data['slash_commands']``.

    First non-empty call wins. No-ops on empty / ``None`` / malformed
    input so a single broken init can't poison the cache.
    """
    if not slash_commands:
        return
    cleaned: set[str] = set()
    for item in slash_commands:
        if not isinstance(item, str):
            continue
        name = item.strip().lstrip("/").strip().lower()
        if name:
            cleaned.add(name)
    if not cleaned:
        return
    global _slashes
    with _lock:
        if _slashes is not None:
            return
        _slashes = cleaned
    log.info(
        "SDK dispatchable slashes (%d): %s",
        len(cleaned),
        ", ".join(f"/{n}" for n in sorted(cleaned)),
    )


def get() -> set[str] | None:
    """Return the cached slash names (lowercase, no leading ``/``).

    ``None`` means we have not observed an init yet — callers should
    skip slash-existence checks in that case rather than guessing.
    """
    with _lock:
        return set(_slashes) if _slashes is not None else None


def reset_for_test() -> None:
    """Test-only: clear the cache so each test starts from a known state."""
    global _slashes
    with _lock:
        _slashes = None
