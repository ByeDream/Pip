"""L2 Reflector + L3 Axiom: consolidate observations into memories, distill axioms.

Phase 1 (L2): merge observations into memories — reinforce, create, decay, forget.
Phase 2 (L3): promote high-stability memories into judgment principles (axioms.md).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pip_agent.types import Memory, Observation

import anthropic

log = logging.getLogger(__name__)

MAX_MEMORIES = 200
PROMOTE_COUNT = 5
PROMOTE_STABILITY = 0.5

CONSOLIDATE_SYSTEM = (
    "You are a memory consolidation engine. Given a list of existing memories "
    "and new observations, produce an updated memory list.\n\n"
    "Rules:\n"
    "- If a new observation matches an existing memory semantically, REINFORCE it "
    "(increment count, update last_reinforced, add context_type to contexts).\n"
    "- If a new observation is novel, CREATE a new memory (count=1).\n"
    "- Existing memories NOT reinforced by any observation: DECAY (count -= 1) "
    "UNLESS source is 'user' (user-sourced memories never decay).\n"
    "- Memories with count <= 0 are FORGOTTEN (remove them).\n"
    "- Calculate stability = unique_contexts / total_cycles for each memory.\n\n"
    "Output a JSON array of memory objects with these fields:\n"
    '  {"id": "...", "text": "...", "count": N, "category": "...", '
    '"first_seen": epoch, "last_reinforced": epoch, '
    '"contexts": ["ctx1", "ctx2"], "total_cycles": N, '
    '"stability": 0.0-1.0, "source": "auto"|"user"}\n\n'
    "Write all text in English.\n"
    "Return ONLY the JSON array, no markdown fences or extra text."
)

AXIOM_SYSTEM = (
    "You are a judgment principle distiller. Given a list of high-stability "
    "behavioral memories about a user, distill them into concise judgment "
    "principles (axioms).\n\n"
    "Each principle should describe HOW the user thinks or decides, not WHO "
    "they are. Focus on decision heuristics, quality standards, and cognitive "
    "patterns that are stable across contexts.\n\n"
    "Output as a markdown list. Each item is one principle, 1-2 sentences.\n"
    "Write all output in English.\n"
    "Return ONLY the markdown list, no extra text or headers."
)


def consolidate(
    client: anthropic.Anthropic,
    observations: list[Observation],
    memories: list[Memory],
    cycle_count: int,
    *,
    model: str = "",
) -> list[Memory]:
    """L2: merge observations into memories. Returns updated memory list."""
    from pip_agent.config import settings
    if not model:
        model = settings.model

    if not observations and not memories:
        return []

    if len(memories) > MAX_MEMORIES:
        memories = sorted(memories, key=lambda m: m.get("count", 0), reverse=True)[:MAX_MEMORIES]

    mem_summary = json.dumps(memories, ensure_ascii=False, default=str)
    obs_summary = json.dumps(observations, ensure_ascii=False, default=str)

    if len(mem_summary) > 40000:
        mem_summary = mem_summary[:40000] + "..."
    if len(obs_summary) > 20000:
        obs_summary = obs_summary[:20000] + "..."

    prompt = (
        f"Current memories ({len(memories)} items):\n{mem_summary}\n\n"
        f"New observations ({len(observations)} items):\n{obs_summary}\n\n"
        f"Current consolidation cycle: {cycle_count}\n"
        "Produce the updated memory list now."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=CONSOLIDATE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        log.warning("consolidate LLM call failed: %s", exc)
        return memories

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        updated = json.loads(text)
    except json.JSONDecodeError:
        log.warning("consolidate: LLM returned invalid JSON, keeping existing memories")
        return memories

    if not isinstance(updated, list):
        return memories

    for mem in updated:
        if not mem.get("id"):
            mem["id"] = uuid.uuid4().hex[:12]

    return updated


def distill_axioms(
    client: anthropic.Anthropic,
    memories: list[Memory],
    *,
    model: str = "",
) -> str:
    """L3: distill high-stability memories into judgment principles.

    Returns markdown text for axioms.md, or empty string if nothing qualifies.
    """
    from pip_agent.config import settings
    if not model:
        model = settings.model

    candidates = [
        m for m in memories
        if m.get("count", 0) >= PROMOTE_COUNT
        and m.get("stability", 0) >= PROMOTE_STABILITY
    ]
    if not candidates:
        return ""

    summary = json.dumps(candidates, ensure_ascii=False, default=str)
    if len(summary) > 30000:
        summary = summary[:30000] + "..."

    prompt = (
        f"High-stability memories ({len(candidates)} items):\n{summary}\n\n"
        "Distill these into judgment principles now."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=AXIOM_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        log.warning("distill_axioms LLM call failed: %s", exc)
        return ""

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    return text.strip()
