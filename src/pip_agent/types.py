"""Shared type definitions for the pip_agent package.

TypedDict classes provide structural typing for JSON-serialised data
that flows between the memory pipeline, routing layer, and agent loop.
"""

from __future__ import annotations

from typing import TypedDict


class Observation(TypedDict, total=False):
    """Single observation from the L1 memory pipeline."""
    ts: float
    text: str
    category: str
    source: str


class Memory(TypedDict, total=False):
    """Consolidated memory entry from the L2 pipeline."""
    text: str
    category: str
    confidence: float
    created_at: float
    updated_at: float
