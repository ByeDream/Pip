"""Shared type definitions for the pip_agent package.

TypedDict classes provide structural typing for JSON-serialised data
that flows between the memory pipeline, routing layer, and agent loop.
"""

from __future__ import annotations

from typing import Literal, TypedDict

MemorySource = Literal["auto", "user"]


class Observation(TypedDict, total=False):
    """Single observation from the L1 memory pipeline."""
    ts: float
    text: str
    category: str
    source: MemorySource


class Memory(TypedDict, total=False):
    """Consolidated memory entry from the L2 pipeline."""
    id: str
    text: str
    category: str
    count: int
    first_seen: float
    last_reinforced: float
    contexts: list[str]
    total_cycles: int
    stability: float
    source: MemorySource
