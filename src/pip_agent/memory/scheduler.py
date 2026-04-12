"""Background daemon thread for scheduling memory pipeline tasks.

L1 Observer runs daily, L2/L3 Consolidation runs every 7 L1 cycles.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic
    from pathlib import Path
    from pip_agent.memory import MemoryStore

log = logging.getLogger(__name__)

POLL_INTERVAL = 60
L1_INTERVAL = 86400       # 24 hours
L2_EVERY_N_REFLECTS = 7


class MemoryScheduler:
    """Daemon scheduler for the memory pipeline.

    Runs in a background thread. Checks every POLL_INTERVAL seconds whether
    L1 (reflect) or L2/L3 (consolidate) tasks are due.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        client: anthropic.Anthropic,
        transcripts_dir: Path,
        stop_event: threading.Event,
        *,
        model: str = "",
    ) -> None:
        self.store = memory_store
        self.client = client
        self.transcripts_dir = transcripts_dir
        self.stop_event = stop_event
        self.model = model

    def run(self) -> None:
        """Main loop. Meant to be run as a daemon thread target."""
        log.debug("MemoryScheduler started for agent %s", self.store.agent_id)
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("MemoryScheduler tick error")
            self.stop_event.wait(POLL_INTERVAL)
        log.debug("MemoryScheduler stopped for agent %s", self.store.agent_id)

    def _tick(self) -> None:
        state = self.store.load_state()
        now = time.time()

        last_reflect = state.get("last_reflect_at", 0)
        if now - last_reflect >= L1_INTERVAL:
            self._run_reflect(state, now)

    def _run_reflect(self, state: dict, now: float) -> None:
        from pip_agent.memory.reflect import reflect

        last_reflect = state.get("last_reflect_at", 0)
        since = last_reflect if last_reflect > 0 else now - L1_INTERVAL

        observations = reflect(
            self.client,
            self.transcripts_dir,
            self.store.agent_id,
            since,
            model=self.model,
        )

        if observations:
            self.store.write_observations(observations)
            log.info(
                "L1 reflect: %d observations for agent %s",
                len(observations), self.store.agent_id,
            )

        reflect_count = state.get("reflect_count_since_consolidate", 0) + 1
        state["last_reflect_at"] = now
        state["reflect_count_since_consolidate"] = reflect_count
        self.store.save_state(state)

        if reflect_count >= L2_EVERY_N_REFLECTS:
            self._run_consolidate(state, now)

    def _run_consolidate(self, state: dict, now: float) -> None:
        from pip_agent.memory.consolidate import consolidate, distill_axioms

        all_observations = self.store.load_all_observations()
        memories = self.store.load_memories()
        cycle_count = state.get("consolidate_cycle", 0) + 1

        updated = consolidate(
            self.client,
            all_observations,
            memories,
            cycle_count,
            model=self.model,
        )
        self.store.save_memories(updated)

        axioms_text = distill_axioms(self.client, updated, model=self.model)
        if axioms_text:
            self.store.save_axioms(axioms_text)

        state["last_consolidate_at"] = now
        state["reflect_count_since_consolidate"] = 0
        state["consolidate_cycle"] = cycle_count
        self.store.save_state(state)

        log.info(
            "L2/L3 consolidate: %d memories, axioms=%s for agent %s",
            len(updated), bool(axioms_text), self.store.agent_id,
        )
